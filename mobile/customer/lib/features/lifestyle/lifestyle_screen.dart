import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

/// Tickets or food from a lifestyle partner, paid from the wallet. The price is the
/// partner's (checked on the server); tickets come back with a code to show at the gate.
class LifestyleScreen extends StatefulWidget {
  const LifestyleScreen({super.key, required this.category});
  final String category; // ticketing | food
  @override
  State<LifestyleScreen> createState() => _LifestyleScreenState();
}

class _LifestyleScreenState extends State<LifestyleScreen> {
  late final ApiClient _api;
  late Future<Map<String, dynamic>> _catalogue;
  late Future<List<Map<String, dynamic>>> _orders;

  bool get _tickets => widget.category == 'ticketing';

  @override
  void initState() {
    super.initState();
    _api = context.read<ApiClient>();
    _load();
  }

  void _load() {
    setState(() {
      _catalogue = _api.get('/lifestyle/${widget.category}').then((r) => Map<String, dynamic>.from(r.data));
      _orders = _api.get('/lifestyle/orders').then((r) => ((r.data as Map)['results'] as List)
          .map((e) => Map<String, dynamic>.from(e))
          .where((o) => o['category'] == widget.category)
          .toList());
    });
  }

  Future<void> _buy(Map<String, dynamic> item) async {
    var qty = 1;
    final ok = await showModalBottomSheet<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, setSheet) => Padding(
          padding: const EdgeInsets.all(20),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(item['name'], style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
            Text(item['description'] ?? '', style: const TextStyle(color: SokoColors.inkMuted)),
            const SizedBox(height: 12),
            Row(children: [
              const Text('Quantity'),
              const Spacer(),
              IconButton(onPressed: qty > 1 ? () => setSheet(() => qty--) : null, icon: const Icon(Icons.remove)),
              Text('$qty', style: const TextStyle(fontSize: 18)),
              IconButton(onPressed: qty < 10 ? () => setSheet(() => qty++) : null, icon: const Icon(Icons.add)),
            ]),
            const SizedBox(height: 8),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: Text('Pay ${item['price_display']} × $qty from wallet'),
            ),
          ]),
        ),
      ),
    );
    if (ok != true || !mounted) return;
    final pin = await askPinFor(context, action: 'buy $qty × ${item['name']}');
    if (pin == null || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      final r = await _api.post('/lifestyle/orders',
          data: {'category': widget.category, 'offering_code': item['code'], 'quantity': qty, 'pin': pin});
      final o = Map<String, dynamic>.from(r.data);
      messenger.showSnackBar(SnackBar(content: Text(o['status'] == 'failed'
          ? "Couldn't be confirmed. You've been refunded."
          : (o['ticket_code'] ?? '') != '' ? 'Confirmed. Ticket code ${o['ticket_code']}' : 'Order confirmed')));
      _load();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: Text(_tickets ? 'Tickets' : 'Food'),
          bottom: TabBar(
            labelColor: Colors.white,
            unselectedLabelColor: Colors.white70,
            indicatorColor: SokoColors.orange,
            tabs: [Tab(text: _tickets ? 'Events' : 'Menu'), Tab(text: _tickets ? 'My tickets' : 'My orders')],
          ),
        ),
        body: TabBarView(children: [
          FutureBuilder<Map<String, dynamic>>(
            future: _catalogue,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) return const Center(child: CircularProgressIndicator());
              if (snap.hasError) return Center(child: Text(apiErrorMessage(snap.error!)));
              final c = snap.data!;
              if (c['available'] != true) {
                return Center(child: Padding(padding: const EdgeInsets.all(24),
                    child: Text(c['message'] ?? 'Not available yet.', textAlign: TextAlign.center)));
              }
              final items = ((c['offerings'] as List?) ?? const []).map((e) => Map<String, dynamic>.from(e)).toList();
              return ListView(children: [
                Padding(padding: const EdgeInsets.all(16),
                    child: Text('From ${c['partner']}', style: const TextStyle(color: SokoColors.inkMuted))),
                for (final i in items)
                  ListTile(
                    leading: Icon(_tickets ? Icons.confirmation_number_outlined : Icons.restaurant_outlined,
                        color: SokoColors.orange),
                    title: Text(i['name']),
                    subtitle: Text(i['description'] ?? ''),
                    trailing: Text(i['price_display'], style: const TextStyle(fontWeight: FontWeight.w700)),
                    onTap: () => _buy(i),
                  ),
              ]);
            },
          ),
          FutureBuilder<List<Map<String, dynamic>>>(
            future: _orders,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) return const Center(child: CircularProgressIndicator());
              final rows = snap.data ?? const [];
              if (rows.isEmpty) return const Center(child: Text('Nothing yet.'));
              return ListView(children: [
                for (final o in rows)
                  ListTile(
                    title: Text('${o['quantity']} × ${o['name']}'),
                    subtitle: Text('${o['status_display']} · ${o['reference']}'),
                    trailing: (o['ticket_code'] ?? '') != ''
                        ? SelectableText(o['ticket_code'], style: const TextStyle(fontFamily: 'monospace', fontWeight: FontWeight.w700))
                        : Text(o['total_display']),
                  ),
              ]);
            },
          ),
        ]),
      ),
    );
  }
}
