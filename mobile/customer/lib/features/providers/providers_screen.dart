import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

/// Save / Invest / Pension — products from licensed partners that SokoPay aggregates
/// (backend: marketplace categories savings | investment | pension, partner connectors
/// in apps/connectors/financial.py). Shows live partner products when connected,
/// otherwise explains who regulates the partners we're bringing on.
class ProvidersScreen extends StatefulWidget {
  const ProvidersScreen({super.key, required this.category});
  final String category; // savings | investment | pension
  @override
  State<ProvidersScreen> createState() => _ProvidersScreenState();
}

class _ProvidersScreenState extends State<ProvidersScreen> {
  static const _meta = {
    'savings': ('Save', Icons.savings_outlined,
        'Grow your money in a savings account with a Bank of Ghana–licensed bank or savings & loans company, '
            'moving money in and out from your SokoPay wallet.'),
    'investment': ('Invest', Icons.show_chart,
        'Invest small amounts in money-market and treasury funds managed by companies licensed by '
            'the Securities and Exchange Commission (SEC).'),
    'pension': ('Pension', Icons.elderly_outlined,
        'Save for retirement with a voluntary (tier-3) pension from an NPRA-licensed trustee, '
            'contributing straight from your wallet.'),
  };

  late Future<List<Map<String, dynamic>>> _products;

  @override
  void initState() {
    super.initState();
    _products = _load();
  }

  Future<List<Map<String, dynamic>>> _load() async {
    final r = await context.read<ApiClient>().get('/marketplace/products', query: {'category': widget.category});
    final data = r.data;
    final list = data is List ? data : (data is Map ? (data['results'] ?? data['products'] ?? []) : []);
    return (list as List).map((e) => Map<String, dynamic>.from(e)).toList();
  }

  @override
  Widget build(BuildContext context) {
    final (title, icon, blurb) = _meta[widget.category] ?? ('Services', Icons.apps, '');
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: FutureBuilder<List<Map<String, dynamic>>>(
        future: _products,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          final products = snap.data ?? const [];
          if (snap.hasError || products.isEmpty) {
            return Padding(
              padding: const EdgeInsets.all(32),
              child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                Icon(icon, size: 64, color: SokoColors.orange),
                const SizedBox(height: 16),
                Text('$title — coming soon', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
                const SizedBox(height: 12),
                Text(blurb, textAlign: TextAlign.center, style: const TextStyle(color: SokoColors.inkMuted)),
                const SizedBox(height: 12),
                const Text('We are partnering with licensed providers. This opens as soon as the first one is live.',
                    textAlign: TextAlign.center, style: TextStyle(color: SokoColors.inkMuted)),
              ]),
            );
          }
          return ListView(padding: const EdgeInsets.all(16), children: [
            Text(blurb, style: const TextStyle(color: SokoColors.inkMuted)),
            const SizedBox(height: 12),
            ...products.map((p) => Card(
                  child: ListTile(
                    leading: Icon(icon, color: SokoColors.orange),
                    title: Text(p['name'] ?? ''),
                    subtitle: Text('${p['provider'] ?? ''}\n${p['summary'] ?? ''}'),
                    isThreeLine: true,
                  ),
                )),
          ]);
        },
      ),
    );
  }
}
