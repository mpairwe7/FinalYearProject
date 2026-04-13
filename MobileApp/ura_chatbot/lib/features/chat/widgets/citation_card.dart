import 'package:flutter/material.dart';

import '../models/chat_models.dart';

/// Expandable list of passage-level citations.
class CitationList extends StatelessWidget {
  final List<Citation> citations;

  const CitationList({super.key, required this.citations});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(bottom: 4),
        dense: true,
        leading: Icon(Icons.format_quote, size: 18, color: cs.primary),
        title: Text(
          '${citations.length} source${citations.length > 1 ? 's' : ''}',
          style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
        ),
        children: citations.map((c) => _CitationTile(citation: c)).toList(),
      ),
    );
  }
}

class _CitationTile extends StatelessWidget {
  final Citation citation;

  const _CitationTile({required this.citation});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Container(
      margin: const EdgeInsets.symmetric(vertical: 2),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: cs.surfaceContainerLow,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: cs.outlineVariant.withValues(alpha: 0.24)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header: ref + source
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: cs.primaryContainer,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  citation.ref,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    color: cs.onPrimaryContainer,
                  ),
                ),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  citation.source,
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                    color: cs.onSurfaceVariant,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),

          // Section / page
          if (citation.section.isNotEmpty || citation.page.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              [
                if (citation.section.isNotEmpty) citation.section,
                if (citation.page.isNotEmpty) 'p. ${citation.page}',
              ].join(' | '),
              style: TextStyle(
                fontSize: 11,
                color: cs.onSurfaceVariant.withValues(alpha: 0.62),
              ),
            ),
          ],

          // Passage excerpt
          if (citation.passage.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              citation.passage,
              style: TextStyle(
                fontSize: 12,
                fontStyle: FontStyle.italic,
                color: cs.onSurfaceVariant.withValues(alpha: 0.78),
                height: 1.4,
              ),
              maxLines: 4,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ],
      ),
    );
  }
}
