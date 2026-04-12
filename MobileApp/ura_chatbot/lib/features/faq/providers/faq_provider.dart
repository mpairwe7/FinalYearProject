import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/config/api_config.dart';
import '../../../core/network/api_client.dart';
import '../models/faq_models.dart';

/// Async provider that fetches all FAQ tags from the backend.
final tagsProvider = FutureProvider<List<TagInfo>>((ref) async {
  final dio = ref.read(apiClientProvider);
  final res = await dio.get<Map<String, dynamic>>(ApiConfig.tags);
  final data = res.data ?? const {};
  final tags = (data['tags'] as List? ?? const [])
      .map((t) => TagInfo.fromJson(t as Map<String, dynamic>))
      .toList();
  return tags;
});

/// Async family provider that fetches FAQs for a specific tag.
final faqItemsProvider =
    FutureProvider.family<List<FAQItem>, String>((ref, tag) async {
  final dio = ref.read(apiClientProvider);
  final res = await dio.get<Map<String, dynamic>>(ApiConfig.faq(tag));
  final data = res.data ?? const {};
  final items = (data['faqs'] as List? ?? const [])
      .map((f) => FAQItem.fromJson(f as Map<String, dynamic>))
      .toList();
  return items;
});
