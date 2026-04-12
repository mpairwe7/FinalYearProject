import 'package:flutter/material.dart';

/// Warning banner shown when escalation_required is true.
class EscalationBanner extends StatelessWidget {
  final String reason;

  const EscalationBanner({super.key, required this.reason});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Container(
      margin: const EdgeInsets.only(top: 4, bottom: 4),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: cs.errorContainer,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.warning_amber_rounded,
            size: 16,
            color: cs.onErrorContainer,
          ),
          const SizedBox(width: 8),
          Flexible(
            child: Text(
              reason.isNotEmpty
                  ? 'This response may need human review: $reason'
                  : 'This response may need human review.',
              style: TextStyle(
                fontSize: 12,
                color: cs.onErrorContainer,
                height: 1.3,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
