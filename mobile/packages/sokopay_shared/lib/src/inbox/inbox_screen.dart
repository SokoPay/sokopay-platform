import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../core/api_client.dart';
import '../core/api_errors.dart';
import '../core/deep_links.dart';
import '../core/theme.dart';

/// The notification inbox — every push we sent, kept so nothing is missed. Shared by
/// the apps; each passes its own [resolve] (the same whitelist its push taps use), so
/// tapping a message marks it read and opens the screen its notification would.
class InboxScreen extends StatefulWidget {
  const InboxScreen({super.key, required this.resolve});
  final PushRouteResolver resolve;
  @override
  State<InboxScreen> createState() => _InboxScreenState();
}

class _InboxScreenState extends State<InboxScreen> {
  late Future<List<Map<String, dynamic>>> _future;

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<List<Map<String, dynamic>>> _load() async {
    final r = await context.read<ApiClient>().get('/notifications');
    return (r.data as List).map((e) => Map<String, dynamic>.from(e)).toList();
  }

  Future<void> _markRead(Map<String, dynamic> n) async {
    if (n['read'] == true) return;
    try {
      await context.read<ApiClient>().post('/notifications/${n['id']}/read');
      if (mounted) setState(() => n['read'] = true);
    } catch (_) {/* best effort */}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Notifications')),
      body: FutureBuilder<List<Map<String, dynamic>>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) return Center(child: Text(apiErrorMessage(snap.error!)));
          final rows = snap.data!;
          if (rows.isEmpty) return const Center(child: Text('Nothing yet.'));
          return RefreshIndicator(
            onRefresh: () async => setState(() => _future = _load()),
            child: ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (_, i) {
                final n = rows[i];
                final unread = n['read'] != true;
                final route = widget.resolve(Map<String, dynamic>.from(n['data'] ?? const {}));
                return ListTile(
                  leading: Icon(_icon(n['kind']), color: unread ? SokoColors.orange : Colors.black38),
                  title: Text(n['title'] ?? '',
                      style: TextStyle(fontWeight: unread ? FontWeight.w700 : FontWeight.w400)),
                  subtitle: Text(n['body'] ?? ''),
                  trailing: route == null ? null : const Icon(Icons.chevron_right),
                  onTap: () {
                    _markRead(n);
                    if (route == null) return;
                    // "Home" isn't pushed on top of itself — just return there.
                    route == '/home' ? context.go(route) : context.push(route);
                  },
                );
              },
            ),
          );
        },
      ),
    );
  }

  IconData _icon(String? kind) => switch (kind) {
        'payment' => Icons.receipt_long,
        'wallet' => Icons.account_balance_wallet,
        'transfer' => Icons.send,
        'security' => Icons.shield,
        _ => Icons.notifications,
      };
}
