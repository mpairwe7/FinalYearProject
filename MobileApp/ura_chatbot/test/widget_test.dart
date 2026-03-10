import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ura_chatbot/core/storage/local_storage.dart';
import 'package:ura_chatbot/main.dart';

void main() {
  testWidgets('App renders with navigation bar', (WidgetTester tester) async {
    // Initialize SharedPreferences with empty values for testing
    SharedPreferences.setMockInitialValues({});
    await LocalStorage.init();

    await tester.pumpWidget(const ProviderScope(child: URAApp()));
    await tester.pumpAndSettle();

    // Verify navigation destinations exist
    expect(find.text('Chat'), findsOneWidget);
    expect(find.text('FAQ'), findsOneWidget);
    expect(find.text('Settings'), findsOneWidget);
  });
}
