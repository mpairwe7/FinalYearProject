class TagInfo {
  final String id;
  final String name;
  final String description;

  const TagInfo({
    required this.id,
    required this.name,
    required this.description,
  });

  factory TagInfo.fromJson(Map<String, dynamic> j) => TagInfo(
    id: j['id'] as String? ?? '',
    name: j['name'] as String? ?? '',
    description: j['description'] as String? ?? '',
  );
}

class FAQItem {
  final String question;
  final String answer;
  final String source;

  const FAQItem({
    required this.question,
    required this.answer,
    required this.source,
  });

  factory FAQItem.fromJson(Map<String, dynamic> j) => FAQItem(
    question: j['question'] as String? ?? '',
    answer: j['answer'] as String? ?? '',
    source: j['source'] as String? ?? '',
  );
}
