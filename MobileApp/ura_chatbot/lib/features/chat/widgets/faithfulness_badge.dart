import 'package:flutter/material.dart';

/// Small badge showing faithfulness score and retrieval mode.
class FaithfulnessBadge extends StatelessWidget {
  final double? score;
  final String retrievalMode;

  const FaithfulnessBadge({
    super.key,
    required this.score,
    required this.retrievalMode,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    // Theme-aware colour based on score
    final (Color bg, Color fg) = switch (score) {
      null => (cs.surfaceContainerHighest, cs.onSurfaceVariant),
      >= 0.7 => (cs.primaryContainer, cs.onPrimaryContainer),
      >= 0.4 => (cs.tertiaryContainer, cs.onTertiaryContainer),
      _ => (cs.errorContainer, cs.onErrorContainer),
    };

    final modeLabel = switch (retrievalMode) {
      'hybrid' => 'RAG',
      'keyword' => 'Keyword',
      'blocked' => 'Blocked',
      'abstained' => 'Abstained',
      _ => retrievalMode,
    };

    return Wrap(
      spacing: 6,
      children: [
        // Retrieval mode chip
        _Chip(label: modeLabel, bg: cs.surfaceContainerHighest, fg: cs.onSurfaceVariant),
        // Faithfulness score
        if (score != null)
          _Chip(
            label: 'Grounding: ${(score! * 100).toInt()}%',
            bg: bg,
            fg: fg,
          ),
      ],
    );
  }
}

class _Chip extends StatelessWidget {
  final String label;
  final Color bg;
  final Color fg;

  const _Chip({required this.label, required this.bg, required this.fg});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        label,
        style: TextStyle(fontSize: 11, color: fg, fontWeight: FontWeight.w500),
      ),
    );
  }
}
