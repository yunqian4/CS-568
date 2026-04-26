/**
 * Default overlay-placement rule.
 *
 * Keep this isolated so the reader can swap display logic without changing the
 * block or chunk data model.
 */

const ASSUMED_PAGE_WIDTH = 760;
const ASSUMED_PAGE_HEIGHT = 980;
const FRAME_PADDING = 4;
const STACK_GAP = 6;
const SUMMARY_LINE_HEIGHT = 18;
const SUMMARY_MAX_LINES = 3;
const KEYWORD_CHIP_HEIGHT = 23;
const KEYWORD_GAP = 4;

export function assignOverlayRepresentations({ chunks, representationsByKind }) {
  const regions = chunks.map((chunk, index) => buildRegion(chunk, index));
  const summary = representationsByKind.get('summary');
  const keywords = representationsByKind.get('keywords');
  const assignments = new Map(regions.map((region) => [region.chunk.chunk_id, []]));

  if (!regions.length || (!summary && !keywords)) {
    return assignments;
  }

  const layout = chooseLayout({ keywords, regions, summary });
  if (layout.summaryRegion && summary) {
    assignments.get(layout.summaryRegion.chunk.chunk_id)?.push(summary);
  }
  if (layout.keywordRegion && keywords) {
    assignments.get(layout.keywordRegion.chunk.chunk_id)?.push(keywords);
  }

  return assignments;
}

function chooseLayout({ keywords, regions, summary }) {
  const summaryCandidates = summary ? regions : [null];
  const keywordCandidates = keywords ? regions : [null];
  let bestLayout = { keywordRegion: null, score: Number.NEGATIVE_INFINITY, summaryRegion: null };

  for (const summaryRegion of summaryCandidates) {
    for (const keywordRegion of keywordCandidates) {
      if (summaryRegion && keywordRegion && isBeforeRegion(keywordRegion, summaryRegion)) {
        continue;
      }

      const score = scoreLayout({ keywordRegion, keywords, summary, summaryRegion });
      if (score > bestLayout.score) {
        bestLayout = { keywordRegion, score, summaryRegion };
      }
    }
  }

  return bestLayout;
}

function scoreLayout({ keywordRegion, keywords, summary, summaryRegion }) {
  const sameRegion = summaryRegion && keywordRegion && summaryRegion === keywordRegion;
  if (sameRegion) {
    return scoreCombinedRegion(summaryRegion, { keywords, summary });
  }

  let score = 0;
  if (summary && summaryRegion) {
    score += scoreSingleRegion(summaryRegion, 'summary', summary);
  }
  if (keywords && keywordRegion) {
    score += scoreSingleRegion(keywordRegion, 'keywords', keywords);
  }
  if (summaryRegion && keywordRegion) {
    score -= Math.abs(keywordRegion.order - summaryRegion.order) * 0.02;
  }
  return score;
}

function scoreCombinedRegion(region, { keywords, summary }) {
  const summaryFootprint = estimateFootprint(summary, 'summary', region.contentWidth);
  const keywordFootprint = estimateFootprint(keywords, 'keywords', region.contentWidth);
  const neededHeight = summaryFootprint.height + STACK_GAP + keywordFootprint.height;
  return scoreFit(region, neededHeight) + 20;
}

function scoreSingleRegion(region, kind, representation) {
  return scoreFit(region, estimateFootprint(representation, kind, region.contentWidth).height);
}

function scoreFit(region, neededHeight) {
  const overflow = Math.max(0, neededHeight - region.contentHeight);
  const fitBonus = overflow === 0 ? 10000 : 0;
  const fillRatio = Math.min(region.contentHeight / Math.max(neededHeight, 1), 4);
  return fitBonus + fillRatio * 100 + region.area * 0.001 - overflow * 40 - region.order * 0.01;
}

function estimateFootprint(representation, kind, contentWidth) {
  if (kind === 'keywords') {
    return estimateKeywordFootprint(representation.items ?? [], contentWidth);
  }
  return estimateSummaryFootprint(representation.text || representation.items?.join(' ') || '', contentWidth);
}

function estimateSummaryFootprint(text, contentWidth) {
  const availableWidth = Math.max(contentWidth - 16, 24);
  const estimatedLines = Math.ceil(String(text).length * 7 / availableWidth);
  const lines = Math.max(1, Math.min(SUMMARY_MAX_LINES, estimatedLines));
  return { height: lines * SUMMARY_LINE_HEIGHT + 10 };
}

function estimateKeywordFootprint(items, contentWidth) {
  let rows = 1;
  let rowWidth = 0;
  const maxWidth = Math.max(contentWidth, 24);

  for (const item of items.filter(Boolean)) {
    const chipWidth = Math.min(String(item).length * 7.2 + 14, maxWidth);
    const nextWidth = rowWidth ? rowWidth + KEYWORD_GAP + chipWidth : chipWidth;
    if (nextWidth > maxWidth && rowWidth) {
      rows += 1;
      rowWidth = chipWidth;
    } else {
      rowWidth = nextWidth;
    }
  }

  return { height: rows * KEYWORD_CHIP_HEIGHT + (rows - 1) * KEYWORD_GAP };
}

function buildRegion(chunk, index) {
  const contentWidth = Math.max(chunk.width * ASSUMED_PAGE_WIDTH - FRAME_PADDING, 1);
  const contentHeight = Math.max(chunk.height * ASSUMED_PAGE_HEIGHT - FRAME_PADDING, 1);
  return {
    area: contentWidth * contentHeight,
    chunk,
    contentHeight,
    contentWidth,
    order: chunk.page_number * 100000 + chunk.y * 1000 + chunk.x + index * 0.0001,
  };
}

function isBeforeRegion(candidate, anchor) {
  if (candidate.chunk.page_number !== anchor.chunk.page_number) {
    return candidate.chunk.page_number < anchor.chunk.page_number;
  }
  return candidate.chunk.y < anchor.chunk.y - 0.001;
}
