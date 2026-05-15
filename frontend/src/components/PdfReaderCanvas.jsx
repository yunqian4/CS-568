import { useEffect, useMemo, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { buildOverlayState } from '../overlays/buildPageOverlays';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;

const BASE_PAGE_SCALE = 1.45;
const RENDER_BUFFER = 1;
const OVERLAY_FONT_MAX = 15;
const OVERLAY_FONT_MIN = 7.5;
const ZOOM_MAX = 3;
const ZOOM_MIN = 0.6;
const ZOOM_STEP = 0.15;

export default function PdfReaderCanvas({ document, representationSettings, visibleRepresentations }) {
  const [pdfInstance, setPdfInstance] = useState(null);
  const [pageSizes, setPageSizes] = useState([]);
  const [visiblePages, setVisiblePages] = useState(() => new Set([1]));
  const [errorMessage, setErrorMessage] = useState('');
  const [zoom, setZoom] = useState(1);
  const stageRef = useRef(null);
  const renderScale = BASE_PAGE_SCALE * zoom;
  const pageDataByNumber = useMemo(
    () => new Map(document.pages.map((page) => [page.page_number, page])),
    [document.pages],
  );
  const overlayState = useMemo(
    () => buildOverlayState(document),
    [document.blocks, document.metadata?.designer_mode, document.metadata?.llm_representations?.enabled, document.pages],
  );
  const representationSettingsMap = useMemo(
    () => buildRepresentationSettingsMap(representationSettings),
    [representationSettings],
  );

  useEffect(() => {
    let cancelled = false;
    let loadingTask = null;

    async function loadPdf() {
      setErrorMessage('');
      setPdfInstance(null);
      setPageSizes([]);
      setVisiblePages(new Set([1]));

      try {
        loadingTask = pdfjsLib.getDocument(document.pdf_url);
        const pdf = await loadingTask.promise;
        if (!cancelled) {
          setPdfInstance(pdf);
        }
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error.message);
        }
      }
    }

    loadPdf();
    return () => {
      cancelled = true;
      loadingTask?.destroy();
    };
  }, [document.pdf_url]);

  useEffect(() => {
    if (!pdfInstance) {
      return undefined;
    }

    let cancelled = false;

    async function updatePageSizes() {
      const sizes = [];
      for (let pageNumber = 1; pageNumber <= pdfInstance.numPages; pageNumber += 1) {
        const page = await pdfInstance.getPage(pageNumber);
        const viewport = page.getViewport({ scale: renderScale });
        sizes.push({
          height: viewport.height,
          pageNumber,
          width: viewport.width,
        });
      }
      if (!cancelled) {
        setPageSizes(sizes);
      }
    }

    updatePageSizes();
    return () => {
      cancelled = true;
    };
  }, [pdfInstance, renderScale]);

  useEffect(() => {
    if (!pageSizes.length) {
      return undefined;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        setVisiblePages((current) => {
          const next = new Set(current);

          for (const entry of entries) {
            const pageNumber = Number(entry.target.getAttribute('data-page-number'));
            if (!pageNumber || !entry.isIntersecting) {
              continue;
            }

            for (
              let candidate = Math.max(1, pageNumber - RENDER_BUFFER);
              candidate <= Math.min(pageSizes.length, pageNumber + RENDER_BUFFER);
              candidate += 1
            ) {
              next.add(candidate);
            }
          }

          return next;
        });
      },
      {
        root: null,
        rootMargin: '320px 0px',
        threshold: 0.01,
      },
    );

    const pageElements = stageRef.current?.querySelectorAll('[data-page-number]') ?? [];
    pageElements.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [pageSizes]);

  if (errorMessage) {
    return <p className="error-banner">{errorMessage}</p>;
  }

  return (
    <>
      <PdfZoomControls zoom={zoom} onZoomChange={setZoom} />
      <section className="pdf-stack" ref={stageRef}>
        {pageSizes.map((page) => (
          <PdfPageCard
            key={page.pageNumber}
            blockCount={overlayState.blockCountByPageNumber.get(page.pageNumber) ?? 0}
            overlays={overlayState.overlaysByPageNumber.get(page.pageNumber) ?? []}
            page={page}
            pageData={pageDataByNumber.get(page.pageNumber)}
            pdf={pdfInstance}
            renderScale={renderScale}
            shouldRender={visiblePages.has(page.pageNumber)}
            representationSettingsMap={representationSettingsMap}
            visibleRepresentations={visibleRepresentations}
          />
        ))}
      </section>
    </>
  );
}

function PdfZoomControls({ onZoomChange, zoom }) {
  return (
    <div className="pdf-zoom-toolbar" aria-label="PDF zoom controls">
      <button
        className="pdf-zoom-button"
        onClick={() => onZoomChange((current) => clampZoom(current - ZOOM_STEP))}
        type="button"
      >
        -
      </button>
      <input
        aria-label="PDF zoom"
        max={ZOOM_MAX}
        min={ZOOM_MIN}
        onChange={(event) => onZoomChange(clampZoom(Number(event.target.value)))}
        step={ZOOM_STEP}
        type="range"
        value={zoom}
      />
      <button
        className="pdf-zoom-button"
        onClick={() => onZoomChange((current) => clampZoom(current + ZOOM_STEP))}
        type="button"
      >
        +
      </button>
      <button className="pdf-zoom-button" onClick={() => onZoomChange(1)} type="button">
        {Math.round(zoom * 100)}%
      </button>
    </div>
  );
}

function PdfPageCard({ blockCount, overlays, page, pageData, pdf, renderScale, representationSettingsMap, shouldRender, visibleRepresentations }) {
  const [cursorPoint, setCursorPoint] = useState(null);
  const [isPointerDown, setIsPointerDown] = useState(false);

  return (
    <article className="pdf-page-card" data-page-number={page.pageNumber}>
      <div className="pdf-page-meta">
        <span>Page {page.pageNumber}</span>
        <span>
          {pageData?.chunks.length ?? 0} chunks | {blockCount} blocks
        </span>
      </div>

      <div
        className="pdf-page-stage"
        onMouseDown={() => setIsPointerDown(true)}
        onMouseLeave={() => {
          setCursorPoint(null);
          setIsPointerDown(false);
        }}
        onMouseMove={(event) => {
          if (event.buttons !== 0 || isPointerDown) {
            return;
          }
          const rect = event.currentTarget.getBoundingClientRect();
          setCursorPoint({
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
          });
        }}
        onMouseUp={() => setIsPointerDown(false)}
        style={{ height: page.height, width: page.width }}
      >
        <PdfPageSurface
          height={page.height}
          pageNumber={page.pageNumber}
          pdf={pdf}
          renderScale={renderScale}
          shouldRender={shouldRender}
          width={page.width}
        />
        <PdfTextLayer
          pageNumber={page.pageNumber}
          pdf={pdf}
          renderScale={renderScale}
          shouldRender={shouldRender}
        />
        <PdfOverlayLayer
          cursorPoint={isPointerDown ? null : cursorPoint}
          overlays={overlays}
          pageHeight={page.height}
          pageWidth={page.width}
          representationSettingsMap={representationSettingsMap}
          shouldRender={shouldRender}
          visibleRepresentations={visibleRepresentations}
        />
      </div>
    </article>
  );
}

function PdfPageSurface({ height, pageNumber, pdf, renderScale, shouldRender, width }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!pdf || !shouldRender || !canvasRef.current) {
      return undefined;
    }

    let cancelled = false;
    let renderTask = null;

    async function renderPage() {
      const page = await pdf.getPage(pageNumber);
      if (cancelled || !canvasRef.current) {
        return;
      }

      const viewport = page.getViewport({ scale: renderScale });
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;

      renderTask = page.render({ canvasContext: context, viewport });
      await renderTask.promise;
    }

    renderPage();
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pageNumber, pdf, renderScale, shouldRender]);

  return shouldRender ? (
    <canvas
      aria-label={`PDF page ${pageNumber}`}
      className="pdf-page-canvas"
      ref={canvasRef}
      style={{ height, width }}
    />
  ) : (
    <div className="pdf-page-placeholder" style={{ height, width }} />
  );
}

function PdfTextLayer({ pageNumber, pdf, renderScale, shouldRender }) {
  const layerRef = useRef(null);

  useEffect(() => {
    if (!pdf || !shouldRender || !layerRef.current) {
      return undefined;
    }

    let cancelled = false;
    let textLayer = null;

    async function renderTextLayer() {
      const page = await pdf.getPage(pageNumber);
      if (cancelled || !layerRef.current) {
        return;
      }

      const viewport = page.getViewport({ scale: renderScale });
      const textContent = await page.getTextContent();
      if (cancelled || !layerRef.current) {
        return;
      }

      const container = layerRef.current;
      container.replaceChildren();
      container.style.setProperty('--scale-factor', `${viewport.scale}`);
      pdfjsLib.setLayerDimensions(container, viewport);

      textLayer = new pdfjsLib.TextLayer({
        container,
        textContentSource: textContent,
        viewport,
      });
      await textLayer.render();
    }

    renderTextLayer();
    return () => {
      cancelled = true;
      textLayer?.cancel();
      layerRef.current?.replaceChildren();
    };
  }, [pageNumber, pdf, renderScale, shouldRender]);

  return shouldRender ? <div className="pdf-text-layer" ref={layerRef} /> : null;
}

function PdfOverlayLayer({ cursorPoint, overlays, pageHeight, pageWidth, representationSettingsMap, shouldRender, visibleRepresentations }) {
  if (!shouldRender) {
    return null;
  }

  return (
    <div className="pdf-overlay-layer">
      {overlays.map((overlay) => {
        const overlayHeight = overlay.height * pageHeight;
        const overlayWidth = overlay.width * pageWidth;
        const visibleOverlayRepresentations = overlay.representations
          .filter((representation) => visibleRepresentations?.[representation.kind] !== false);
        const typographyStyle = buildOverlayTypographyStyle({
          height: overlayHeight,
          representations: visibleOverlayRepresentations,
          width: overlayWidth,
        });

        return (
          <div
            className="overlay-frame"
            key={overlay.overlayId}
            style={{
              height: `${overlayHeight}px`,
              left: `${overlay.x * pageWidth}px`,
              top: `${overlay.y * pageHeight}px`,
              width: `${overlayWidth}px`,
            }}
          >
            <div
              className="overlay-representation-stack"
              style={{
                ...typographyStyle,
                opacity: getOverlayOpacity(overlay, cursorPoint, pageWidth, pageHeight),
              }}
            >
              {visibleOverlayRepresentations.map((representation) => (
                <OverlayRepresentation
                  key={`${overlay.overlayId}:${representation.kind}`}
                  representation={representation}
                  representationSettingsMap={representationSettingsMap}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function OverlayRepresentation({ representation, representationSettingsMap }) {
  if (representation.kind === 'keywords') {
    return <KeywordRepresentation representation={representation} representationSettingsMap={representationSettingsMap} />;
  }

  const content = renderRepresentation(representation);
  if (!content) {
    return null;
  }

  return (
    <span
      className={`overlay-badge overlay-badge-${cssSafeKind(representation.kind)}`}
      style={{ backgroundColor: representationBackground(representation, representationSettingsMap) }}
    >
      {content}
    </span>
  );
}

function KeywordRepresentation({ representation, representationSettingsMap }) {
  const keywords = (representation.items?.length ? representation.items : String(representation.value ?? '').split(',')).map((item) => item.trim()).filter(Boolean);
  if (!keywords.length) {
    return null;
  }

  return (
    <span
      className="overlay-badge overlay-badge-keywords"
      style={{ '--overlay-representation-background': representationBackground(representation, representationSettingsMap) }}
    >
      {keywords.map((keyword, index) => (
        <span className="overlay-keyword" key={`${keyword}:${index}`}>
          {keyword}
        </span>
      ))}
    </span>
  );
}

function renderRepresentation(representation) {
  if (representation.value) {
    return representation.value;
  }

  if (representation.text) {
    return representation.text;
  }

  if (representation.items?.length) {
    return representation.items.join(' | ');
  }

  return '';
}

function buildOverlayTypographyStyle({ height, representations, width }) {
  if (!representations.length) {
    return {};
  }

  const hasBlockLabel = representations.some((representation) => representation.kind === 'block-label');
  const typography = chooseOverlayTypography({
    height: Math.max(height - 4, 1),
    representations,
    width: Math.max(width - 4, 1),
  });

  return {
    '--overlay-badge-padding-x': `${roundCssPx(Math.max(4, typography.fontSize * 0.54))}px`,
    '--overlay-badge-padding-y': `${roundCssPx(Math.max(2, typography.fontSize * 0.28))}px`,
    '--overlay-font-size': `${roundCssPx(typography.fontSize)}px`,
    '--overlay-keyword-font-size': `${roundCssPx(typography.fontSize)}px`,
    '--overlay-keyword-gap': `${roundCssPx(Math.max(2, typography.fontSize * 0.3))}px`,
    '--overlay-keyword-padding-x': `${roundCssPx(Math.max(4, typography.fontSize * 0.5))}px`,
    '--overlay-keyword-padding-y': `${roundCssPx(Math.max(2, typography.fontSize * 0.2))}px`,
    '--overlay-summary-font-size': `${roundCssPx(typography.fontSize)}px`,
    '--overlay-summary-lines': String(typography.summaryLines),
    '--overlay-block-label-font-size': `${roundCssPx(Math.max(6.5, Math.min(10, typography.fontSize * 0.78)))}px`,
    '--overlay-stack-gap': `${roundCssPx(Math.max(hasBlockLabel ? 1 : 2, typography.fontSize * (hasBlockLabel ? 0.2 : 0.38)))}px`,
  };
}

function chooseOverlayTypography({ height, representations, width }) {
  let low = OVERLAY_FONT_MIN;
  let high = OVERLAY_FONT_MAX;
  let best = low;
  let bestMeasurement = measureVisibleRepresentations(representations, best, width);

  for (let step = 0; step < 8; step += 1) {
    const candidate = (low + high) / 2;
    const measurement = measureVisibleRepresentations(representations, candidate, width);
    if (measurement.height <= height) {
      best = candidate;
      bestMeasurement = measurement;
      low = candidate;
    } else {
      high = candidate;
    }
  }

  return {
    fontSize: best,
    summaryLines: bestMeasurement.summaryLines,
  };
}

function measureVisibleRepresentations(representations, fontSize, width) {
  let height = 0;
  let renderedCount = 0;
  let summaryLines = 3;
  const stackGap = Math.max(2, fontSize * 0.38);

  for (const representation of representations) {
    const footprint = measureRepresentationFootprint(representation, fontSize, width);
    if (!footprint.height) {
      continue;
    }

    if (renderedCount) {
      height += stackGap;
    }
    height += footprint.height;
    renderedCount += 1;

    if (!['keywords', 'block-label'].includes(representation.kind)) {
      summaryLines = footprint.lines;
    }
  }

  return { height, summaryLines };
}

function measureRepresentationFootprint(representation, fontSize, width) {
  if (representation.kind === 'keywords') {
    return measureKeywordFootprint(representation.items ?? [], fontSize, width);
  }
  if (representation.kind === 'block-label') {
    return measureTextBadgeFootprint(renderRepresentation(representation), Math.max(6.5, fontSize * 0.78), width, 1);
  }
  return measureTextBadgeFootprint(renderRepresentation(representation), fontSize, width);
}

function measureTextBadgeFootprint(text, fontSize, width, maxLines = 3) {
  if (!text) {
    return { height: 0, lines: 1 };
  }

  const paddingX = Math.max(4, fontSize * 0.54);
  const paddingY = Math.max(2, fontSize * 0.28);
  const availableWidth = Math.max(width - paddingX * 2, 8);
  const lineHeight = fontSize * 1.35;
  const estimatedLines = Math.ceil(String(text).length * fontSize * 0.52 / availableWidth);
  const lines = Math.max(1, Math.min(maxLines, estimatedLines));
  return {
    height: lines * lineHeight + paddingY * 2,
    lines,
  };
}

function measureKeywordFootprint(items, fontSize, width) {
  const keywords = items.filter(Boolean);
  if (!keywords.length) {
    return { height: 0 };
  }

  const gap = Math.max(2, fontSize * 0.3);
  const lineHeight = fontSize * 1.25;
  const paddingX = Math.max(4, fontSize * 0.5);
  const paddingY = Math.max(2, fontSize * 0.2);
  const chipHeight = lineHeight + paddingY * 2 + 2;
  const maxWidth = Math.max(width, 8);
  let rowWidth = 0;
  let rows = 1;

  for (const keyword of keywords) {
    const chipWidth = Math.min(String(keyword).length * fontSize * 0.55 + paddingX * 2 + 2, maxWidth);
    const nextWidth = rowWidth ? rowWidth + gap + chipWidth : chipWidth;
    if (nextWidth > maxWidth && rowWidth) {
      rows += 1;
      rowWidth = chipWidth;
    } else {
      rowWidth = nextWidth;
    }
  }

  return { height: rows * chipHeight + (rows - 1) * gap };
}

function roundCssPx(value) {
  return Math.round(value * 100) / 100;
}

function buildRepresentationSettingsMap(settings) {
  const map = new Map();
  for (const setting of settings ?? []) {
    map.set(setting.name, setting);
    if (setting.id) {
      map.set(setting.id, setting);
    }
  }
  return map;
}

function representationBackground(representation, settingsMap) {
  const setting = settingsMap.get(representation.kind);
  const color = setting?.background_color
    ?? representation.background_color
    ?? '#263238';
  const opacity = setting?.background_opacity
    ?? representation.background_opacity
    ?? 1;
  return colorWithOpacity(color, opacity);
}

function colorWithOpacity(color, opacity) {
  const alpha = Math.min(Math.max(Number(opacity), 0), 1);
  const hex = String(color || '').trim();
  const match = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!match) {
    return color || '#263238';
  }
  const [, red, green, blue] = match;
  return `rgba(${parseInt(red, 16)}, ${parseInt(green, 16)}, ${parseInt(blue, 16)}, ${alpha})`;
}

function clampZoom(value) {
  if (!Number.isFinite(value)) {
    return 1;
  }
  return Math.min(Math.max(Number(value), ZOOM_MIN), ZOOM_MAX);
}

function cssSafeKind(kind) {
  return String(kind).toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-|-$/g, '') || 'representation';
}

function getOverlayOpacity(overlay, cursorPoint, pageWidth, pageHeight) {
  if (!cursorPoint) {
    return 1;
  }

  const rect = {
    left: overlay.x * pageWidth,
    top: overlay.y * pageHeight,
    right: (overlay.x + overlay.width) * pageWidth,
    bottom: (overlay.y + overlay.height) * pageHeight,
  };
  const distance = distanceToRect(cursorPoint.x, cursorPoint.y, rect);
  const fadeRadius = 96;

  if (distance <= 0) {
    return 0;
  }
  if (distance >= fadeRadius) {
    return 1;
  }
  return distance / fadeRadius;
}

function distanceToRect(x, y, rect) {
  const dx = Math.max(rect.left - x, 0, x - rect.right);
  const dy = Math.max(rect.top - y, 0, y - rect.bottom);
  return Math.hypot(dx, dy);
}
