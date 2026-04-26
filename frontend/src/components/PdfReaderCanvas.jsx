import { useEffect, useMemo, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { buildOverlayState } from '../overlays/buildPageOverlays';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;

const PAGE_SCALE = 1.45;
const RENDER_BUFFER = 1;
const OVERLAY_FONT_MAX = 15;
const OVERLAY_FONT_MIN = 7.5;

export default function PdfReaderCanvas({ document, visibleRepresentations }) {
  const [pdfInstance, setPdfInstance] = useState(null);
  const [pageSizes, setPageSizes] = useState([]);
  const [visiblePages, setVisiblePages] = useState(() => new Set([1]));
  const [errorMessage, setErrorMessage] = useState('');
  const stageRef = useRef(null);
  const pageDataByNumber = useMemo(
    () => new Map(document.pages.map((page) => [page.page_number, page])),
    [document.pages],
  );
  const overlayState = useMemo(
    () => buildOverlayState(document),
    [document.blocks, document.metadata?.llm_representations?.enabled, document.pages],
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
        const sizes = [];

        for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
          const page = await pdf.getPage(pageNumber);
          const viewport = page.getViewport({ scale: PAGE_SCALE });
          sizes.push({
            height: viewport.height,
            pageNumber,
            width: viewport.width,
          });
        }

        if (!cancelled) {
          setPdfInstance(pdf);
          setPageSizes(sizes);
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
    <section className="pdf-stack" ref={stageRef}>
      {pageSizes.map((page) => (
        <PdfPageCard
          key={page.pageNumber}
          blockCount={overlayState.blockCountByPageNumber.get(page.pageNumber) ?? 0}
          overlays={overlayState.overlaysByPageNumber.get(page.pageNumber) ?? []}
          page={page}
          pageData={pageDataByNumber.get(page.pageNumber)}
          pdf={pdfInstance}
          shouldRender={visiblePages.has(page.pageNumber)}
          visibleRepresentations={visibleRepresentations}
        />
      ))}
    </section>
  );
}

function PdfPageCard({ blockCount, overlays, page, pageData, pdf, shouldRender, visibleRepresentations }) {
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
          shouldRender={shouldRender}
          width={page.width}
        />
        <PdfTextLayer
          pageNumber={page.pageNumber}
          pdf={pdf}
          shouldRender={shouldRender}
        />
        <PdfOverlayLayer
          cursorPoint={isPointerDown ? null : cursorPoint}
          overlays={overlays}
          pageHeight={page.height}
          pageWidth={page.width}
          shouldRender={shouldRender}
          visibleRepresentations={visibleRepresentations}
        />
      </div>
    </article>
  );
}

function PdfPageSurface({ height, pageNumber, pdf, shouldRender, width }) {
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

      const viewport = page.getViewport({ scale: PAGE_SCALE });
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
  }, [pageNumber, pdf, shouldRender]);

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

function PdfTextLayer({ pageNumber, pdf, shouldRender }) {
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

      const viewport = page.getViewport({ scale: PAGE_SCALE });
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
  }, [pageNumber, pdf, shouldRender]);

  return shouldRender ? <div className="pdf-text-layer" ref={layerRef} /> : null;
}

function PdfOverlayLayer({ cursorPoint, overlays, pageHeight, pageWidth, shouldRender, visibleRepresentations }) {
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
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function OverlayRepresentation({ representation }) {
  if (representation.kind === 'keywords') {
    return <KeywordRepresentation representation={representation} />;
  }

  const content = renderRepresentation(representation);
  if (!content) {
    return null;
  }

  return (
    <span className={`overlay-badge overlay-badge-${representation.kind}`}>
      {content}
    </span>
  );
}

function KeywordRepresentation({ representation }) {
  const keywords = (representation.items ?? []).filter(Boolean);
  if (!keywords.length) {
    return null;
  }

  return (
    <span className="overlay-badge overlay-badge-keywords">
      {keywords.map((keyword, index) => (
        <span className="overlay-keyword" key={`${keyword}:${index}`}>
          {keyword}
        </span>
      ))}
    </span>
  );
}

function renderRepresentation(representation) {
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
    '--overlay-stack-gap': `${roundCssPx(Math.max(2, typography.fontSize * 0.38))}px`,
    '--overlay-summary-font-size': `${roundCssPx(typography.fontSize)}px`,
    '--overlay-summary-lines': String(typography.summaryLines),
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
    const footprint = representation.kind === 'keywords'
      ? measureKeywordFootprint(representation.items ?? [], fontSize, width)
      : measureTextBadgeFootprint(renderRepresentation(representation), fontSize, width);
    if (!footprint.height) {
      continue;
    }

    if (renderedCount) {
      height += stackGap;
    }
    height += footprint.height;
    renderedCount += 1;

    if (representation.kind !== 'keywords') {
      summaryLines = footprint.lines;
    }
  }

  return { height, summaryLines };
}

function measureTextBadgeFootprint(text, fontSize, width) {
  if (!text) {
    return { height: 0, lines: 1 };
  }

  const paddingX = Math.max(4, fontSize * 0.54);
  const paddingY = Math.max(2, fontSize * 0.28);
  const availableWidth = Math.max(width - paddingX * 2, 8);
  const lineHeight = fontSize * 1.35;
  const estimatedLines = Math.ceil(String(text).length * fontSize * 0.52 / availableWidth);
  const lines = Math.max(1, Math.min(3, estimatedLines));
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
