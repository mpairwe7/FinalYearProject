import 'package:flutter/material.dart';

/// Shown when the chat is empty — quick-tap starter questions.
class StarterPrompts extends StatelessWidget {
  final ValueChanged<String> onTap;

  const StarterPrompts({super.key, required this.onTap});

  static const _prompts = [
    (
      icon: Icons.receipt_long,
      text: 'How do I register for TIN?',
    ),
    (
      icon: Icons.percent,
      text: 'What is the VAT rate in Uganda?',
    ),
    (
      icon: Icons.gavel,
      text: 'What are the penalties for late filing?',
    ),
    (
      icon: Icons.phone_android,
      text: 'How do I use EFRIS?',
    ),
    (
      icon: Icons.payment,
      text: 'How can I pay taxes online?',
    ),
    (
      icon: Icons.business,
      text: 'What is corporate income tax?',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tt = Theme.of(context).textTheme;

    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.smart_toy, size: 56, color: cs.primary),
            const SizedBox(height: 16),
            Text(
              'URA Tax Assistant',
              style: tt.headlineSmall?.copyWith(
                fontWeight: FontWeight.w600,
                color: cs.onSurface,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Ask me anything about Uganda Revenue Authority taxes, registration, filing, and more.',
              textAlign: TextAlign.center,
              style: tt.bodyMedium?.copyWith(color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: 28),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              alignment: WrapAlignment.center,
              children: _prompts
                  .map(
                    (p) => ActionChip(
                      avatar: Icon(p.icon, size: 18),
                      label: Text(
                        p.text,
                        style: const TextStyle(fontSize: 13),
                      ),
                      onPressed: () => onTap(p.text),
                    ),
                  )
                  .toList(),
            ),
          ],
        ),
      ),
    );
  }
}
