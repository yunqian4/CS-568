import { assignOverlayRepresentations } from './rules';

/**
 * Build overlay containers from paragraph blocks and source chunks.
 */
export function buildOverlayState(document) {
  const llmRepresentationsEnabled = document?.metadata?.llm_representations?.enabled === true;
  const chunksById = new Map();
  for (const page of document.pages ?? []) {
    for (const chunk of page.chunks ?? []) {
      chunksById.set(chunk.chunk_id, chunk);
    }
  }

  const overlaysByPageNumber = new Map();
  const blockCountByPageNumber = new Map();

  for (const block of document.blocks ?? []) {
    blockCountByPageNumber.set(
      block.page_number,
      (blockCountByPageNumber.get(block.page_number) ?? 0) + 1,
    );

    let representationsByKind = new Map(
      (block.representations ?? []).map((representation) => [representation.kind, representation]),
    );
    const chunks = (block.chunk_ids ?? [])
      .map((chunkId) => chunksById.get(chunkId))
      .filter(Boolean);

    if (!llmRepresentationsEnabled && !representationsByKind.size) {
      const fallbackChunk = chunks.find((chunk) => chunk.keywords?.length);
      if (fallbackChunk) {
        representationsByKind = new Map([[
          'keywords',
          {
            items: fallbackChunk.keywords.map((keyword) => keyword.label),
            kind: 'keywords',
            label: 'Keywords',
            text: null,
          },
        ]]);
      }
    }

    const representationAssignments = assignOverlayRepresentations({ chunks, representationsByKind });

    chunks.forEach((chunk) => {
      const chunkId = chunk.chunk_id;
      const representations = representationAssignments.get(chunkId) ?? [];

      const pageOverlays = overlaysByPageNumber.get(chunk.page_number) ?? [];
      pageOverlays.push({
        blockId: block.block_id,
        chunkId,
        overlayId: `${block.block_id}:${chunkId}`,
        pageNumber: chunk.page_number,
        representations,
        x: chunk.x,
        y: chunk.y,
        width: chunk.width,
        height: chunk.height,
      });
      overlaysByPageNumber.set(chunk.page_number, pageOverlays);
    });
  }

  for (const overlays of overlaysByPageNumber.values()) {
    overlays.sort((first, second) => {
      if (first.y !== second.y) {
        return first.y - second.y;
      }
      return first.x - second.x;
    });
  }

  return { blockCountByPageNumber, overlaysByPageNumber };
}
