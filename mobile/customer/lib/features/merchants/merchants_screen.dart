import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../scan/pay_merchant_screen.dart';

/// Merchants: "Pay new merchant" (scan or type a code), then two tabs:
/// RECENTLY PAID (tap to pay again, star to save) and SAVED MERCHANTS.
/// Every payment re-resolves the merchant code, so a merchant that has been
/// suspended since can't be paid from a saved entry.
class MerchantsScreen extends StatefulWidget {
  const MerchantsScreen({super.key});
  @override
  State<MerchantsScreen> createState() => _MerchantsScreenState();
}

class _MerchantsScreenState extends State<MerchantsScreen> with SingleTickerProviderStateMixin {
  late final ApiClient _api;
  late final TabController _tabs;
  late Future<List<Map<String, dynamic>>> _recent;
  late Future<List<Map<String, dynamic>>> _saved;

  @override
  void initState() {
    super.initState();
    _api = context.read<ApiClient>();
    _tabs = TabController(length: 2, vsync: this);
    _load();
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  void _load() {
    setState(() {
      _recent = _api.get('/wallet/merchants/recent').then((r) => _list(r.data));
      _saved = _api.get('/wallet/saved', query: {'kind': 'merchant'}).then((r) => _list(r.data));
    });
  }

  static List<Map<String, dynamic>> _list(dynamic data) =>
      ((data as Map)['results'] as List).map((e) => Map<String, dynamic>.from(e)).toList();

  Future<void> _payCode(String code) async {
    if (code.isEmpty) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      final r = await _api.get('/pay/resolve', query: {'code': code});
      if (!mounted) return;
      await Navigator.of(context).push<bool>(MaterialPageRoute(
        builder: (_) => PayMerchantScreen(target: Map<String, dynamic>.from(r.data), code: code),
      ));
      _load();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e, fallback: "That merchant can't be paid right now."))));
    }
  }

  Future<void> _save(String code) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await _api.post('/wallet/saved', data: {'kind': 'merchant', 'value': code});
      messenger.showSnackBar(const SnackBar(content: Text('Merchant saved')));
      _load();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  Future<void> _remove(int id) async {
    try {
      await _api.delete('/wallet/saved/$id');
    } catch (_) {/* refreshed below either way */}
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: SokoColors.bg,
      body: Column(children: [
        Stack(children: [
          Container(
            height: 190,
            decoration: const BoxDecoration(
              gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter,
                  colors: [SokoColors.ink, Color(0xFF3A3435)]),
              borderRadius: BorderRadius.vertical(bottom: Radius.elliptical(400, 40)),
            ),
          ),
          SafeArea(
            bottom: false,
            child: Column(children: [
              Row(children: [
                IconButton(
                  icon: const Icon(Icons.arrow_back_ios_new, color: SokoColors.orange),
                  onPressed: () => context.pop(),
                ),
                const Expanded(
                  child: Text('Merchants', textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w700)),
                ),
                const SizedBox(width: 48),
              ]),
              const SizedBox(height: 20),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Material(
                  color: SokoColors.surface,
                  elevation: 3,
                  shadowColor: const Color(0x33231F20),
                  borderRadius: BorderRadius.circular(16),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(16),
                    onTap: () async {
                      await context.push('/scan');
                      _load();
                    },
                    child: const SizedBox(
                      height: 110,
                      width: double.infinity,
                      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                        Icon(Icons.storefront_outlined, size: 34, color: SokoColors.ink),
                        SizedBox(height: 10),
                        Text('Pay new merchant', style: TextStyle(fontSize: 15, color: SokoColors.inkMuted)),
                      ]),
                    ),
                  ),
                ),
              ),
            ]),
          ),
        ]),
        Container(
          color: SokoColors.surface,
          child: TabBar(
            controller: _tabs,
            labelColor: SokoColors.ink,
            unselectedLabelColor: Colors.black38,
            labelStyle: const TextStyle(fontWeight: FontWeight.w800, letterSpacing: 0.5),
            indicator: const UnderlineTabIndicator(
              borderSide: BorderSide(color: SokoColors.orange, width: 4),
              insets: EdgeInsets.symmetric(horizontal: 24),
            ),
            tabs: const [Tab(text: 'RECENTLY PAID'), Tab(text: 'SAVED MERCHANTS')],
          ),
        ),
        Expanded(
          child: TabBarView(controller: _tabs, children: [
            _ListTab(
              future: _recent,
              empty: 'Merchants you pay will show here.',
              header: TextButton(
                onPressed: () => context.push('/activity?filter=payments'),
                child: const Text('See all'),
              ),
              itemBuilder: (m) => _MerchantCard(
                title: m['merchant_name'] ?? '',
                lines: [
                  DateFormat('dd MMM yyyy | HH:mm').format(DateTime.parse(m['created_at']).toLocal()),
                  'Reference: ${m['reference']}',
                ],
                amount: '-${m['amount_display']}',
                onTap: () => context.push('/payments/${m['reference']}'),
                action: IconButton(
                  tooltip: m['saved'] == true ? 'Saved' : 'Save merchant',
                  icon: Icon(m['saved'] == true ? Icons.star : Icons.star_border, color: SokoColors.orange),
                  onPressed: m['saved'] == true || (m['merchant_code'] ?? '') == '' ? null : () => _save(m['merchant_code']),
                ),
                payAgain: (m['merchant_code'] ?? '') == '' ? null : () => _payCode(m['merchant_code']),
              ),
            ),
            _ListTab(
              future: _saved,
              empty: 'Tap the star on a merchant you paid to save it here.',
              itemBuilder: (s) => _MerchantCard(
                title: s['label'] ?? '',
                lines: ['Merchant code ${s['value']}'],
                onTap: () => _payCode(s['value']),
                action: IconButton(
                  tooltip: 'Remove',
                  icon: const Icon(Icons.delete_outline, color: SokoColors.inkMuted),
                  onPressed: () => _remove(s['id'] as int),
                ),
              ),
            ),
          ]),
        ),
      ]),
    );
  }
}

class _ListTab extends StatelessWidget {
  const _ListTab({required this.future, required this.itemBuilder, required this.empty, this.header});
  final Future<List<Map<String, dynamic>>> future;
  final Widget Function(Map<String, dynamic>) itemBuilder;
  final String empty;
  final Widget? header;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<Map<String, dynamic>>>(
      future: future,
      builder: (context, snap) {
        if (snap.connectionState != ConnectionState.done) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snap.hasError) return Center(child: Text(apiErrorMessage(snap.error!)));
        final items = snap.data!;
        return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 100), children: [
          if (header != null) Align(alignment: Alignment.centerRight, child: header),
          if (items.isEmpty)
            Padding(padding: const EdgeInsets.all(32), child: Text(empty, textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.black45))),
          ...items.map(itemBuilder),
        ]);
      },
    );
  }
}

class _MerchantCard extends StatelessWidget {
  const _MerchantCard({required this.title, required this.lines, required this.onTap, this.amount, this.action,
      this.payAgain});
  final String title;
  final List<String> lines;
  final String? amount;
  final VoidCallback onTap;
  final Widget? action;
  final VoidCallback? payAgain;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 14),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(18, 14, 6, 10),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(title.toUpperCase(), maxLines: 1, overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                const SizedBox(height: 4),
                for (final l in lines)
                  Text(l, style: const TextStyle(fontSize: 12, color: SokoColors.inkMuted)),
                if (payAgain != null)
                  TextButton(
                    style: TextButton.styleFrom(padding: EdgeInsets.zero, minimumSize: const Size(0, 32)),
                    onPressed: payAgain,
                    child: const Text('Pay again'),
                  ),
              ]),
            ),
            Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
              if (amount != null)
                Padding(
                  padding: const EdgeInsets.only(right: 12),
                  child: Text(amount!, style: const TextStyle(color: SokoColors.danger, fontWeight: FontWeight.w700)),
                ),
              if (action != null) action!,
            ]),
          ]),
        ),
      ),
    );
  }
}
