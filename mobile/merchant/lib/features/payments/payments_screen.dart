import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../business/merchant_service.dart';

/// Payment history: status + period filters, a period summary (money roles only),
/// newest first with infinite scroll (server cursor paging — stable even while new
/// payments arrive), grouped by day, pull to refresh. Tap a payment for details.
class PaymentsScreen extends StatefulWidget {
  const PaymentsScreen({super.key});
  @override
  State<PaymentsScreen> createState() => _PaymentsScreenState();
}

class _PaymentsScreenState extends State<PaymentsScreen> {
  static const _statuses = <String?, String>{
    null: 'All', 'paid': 'Paid', 'pending': 'Pending', 'failed': 'Failed', 'refunded': 'Refunded',
  };
  static const _periods = <String?, String>{
    'today': 'Today', '7d': 'Last 7 days', '30d': 'Last 30 days', null: 'All time',
  };

  late final MerchantService _service;
  final _scroll = ScrollController();
  final _items = <PaymentInfo>[];
  String? _status;
  String? _period = 'today';
  String? _cursor;
  PaymentsSummary? _summary;
  bool _loading = false;
  bool _done = false;
  String? _error;
  int _generation = 0; // ignores responses from a filter the user has since changed

  @override
  void initState() {
    super.initState();
    _service = MerchantService(context.read<ApiClient>());
    _scroll.addListener(() {
      if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 300) _loadMore();
    });
    _reload();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _reload() async {
    _generation++;
    setState(() {
      _items.clear();
      _cursor = null;
      _summary = null;
      _done = false;
      _error = null;
      _loading = false;
    });
    await _loadMore();
  }

  Future<void> _loadMore() async {
    if (_loading || _done) return;
    final gen = _generation;
    setState(() => _loading = true);
    try {
      final page = await _service.payments(status: _status, period: _period, cursor: _cursor);
      if (!mounted || gen != _generation) return;
      setState(() {
        _items.addAll(page.results);
        if (_cursor == null) _summary = page.summary;
        _cursor = page.nextCursor;
        _done = page.nextCursor == null;
      });
    } catch (e) {
      if (mounted && gen == _generation) setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted && gen == _generation) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Payments')),
      body: Column(children: [
        _filters(),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _reload,
            child: ListView(
              controller: _scroll,
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
              children: [
                if (_summary != null) _SummaryCard(summary: _summary!, period: _periods[_period]!),
                ..._grouped(),
                if (_loading) const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator())),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(children: [
                      Text(_error!, textAlign: TextAlign.center),
                      TextButton(onPressed: _loadMore, child: const Text('Try again')),
                    ]),
                  ),
                if (!_loading && _error == null && _items.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(40),
                    child: Text('No payments here yet.', textAlign: TextAlign.center, style: TextStyle(color: Colors.black45)),
                  ),
              ],
            ),
          ),
        ),
      ]),
    );
  }

  Widget _filters() {
    return Container(
      color: SokoColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: _statuses.entries
                .map((e) => Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        label: Text(e.value),
                        selected: _status == e.key,
                        onSelected: (_) {
                          setState(() => _status = e.key);
                          _reload();
                        },
                      ),
                    ))
                .toList(),
          ),
        ),
        DropdownButton<String?>(
          value: _period,
          underline: const SizedBox.shrink(),
          items: _periods.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
          onChanged: (v) {
            setState(() => _period = v);
            _reload();
          },
        ),
      ]),
    );
  }

  List<Widget> _grouped() {
    final out = <Widget>[];
    String? lastDay;
    final dayFmt = DateFormat('EEEE d MMMM');
    final now = DateTime.now();
    for (final p in _items) {
      final d = p.createdAt;
      final isToday = d.year == now.year && d.month == now.month && d.day == now.day;
      final day = isToday ? 'Today' : dayFmt.format(d);
      if (day != lastDay) {
        out.add(Padding(
          padding: const EdgeInsets.only(top: 16, bottom: 4),
          child: Text(day, style: const TextStyle(fontWeight: FontWeight.w600, color: SokoColors.inkMuted)),
        ));
        lastDay = day;
      }
      out.add(_PaymentTile(payment: p, onTap: () => context.push('/payments/${p.reference}')));
    }
    return out;
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({required this.summary, required this.period});
  final PaymentsSummary summary;
  final String period;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: SokoColors.ink, borderRadius: BorderRadius.circular(12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('$period · ${summary.paidCount} paid', style: const TextStyle(color: Colors.white70)),
        const SizedBox(height: 6),
        Text(summary.gross, style: const TextStyle(color: Colors.white, fontSize: 26, fontWeight: FontWeight.w700)),
        const SizedBox(height: 4),
        Text('Fees ${summary.fees} · You receive ${summary.net}', style: const TextStyle(color: Colors.white70)),
      ]),
    );
  }
}

class _PaymentTile extends StatelessWidget {
  const _PaymentTile({required this.payment, required this.onTap});
  final PaymentInfo payment;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = payment;
    final subtitle = [
      DateFormat('HH:mm').format(p.createdAt),
      p.method,
      if (p.note.isNotEmpty) p.note,
    ].join(' · ');
    return ListTile(
      onTap: onTap,
      contentPadding: EdgeInsets.zero,
      leading: CircleAvatar(
        backgroundColor: paymentStatusColor(p.status).withValues(alpha: 0.12),
        child: Icon(paymentStatusIcon(p.status), color: paymentStatusColor(p.status)),
      ),
      title: Row(children: [
        Text(p.amountDisplay, style: const TextStyle(fontWeight: FontWeight.w600)),
        if (p.isTest) ...[
          const SizedBox(width: 6),
          const Text('TEST', style: TextStyle(fontSize: 10, color: SokoColors.warning, fontWeight: FontWeight.w700)),
        ],
      ]),
      subtitle: Text(subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: Text(p.statusDisplay, style: TextStyle(color: paymentStatusColor(p.status), fontWeight: FontWeight.w600)),
    );
  }
}

Color paymentStatusColor(String status) => switch (status) {
      'succeeded' => SokoColors.success,
      'failed' => SokoColors.danger,
      'refunded' => SokoColors.inkMuted,
      _ => SokoColors.warning,
    };

IconData paymentStatusIcon(String status) => switch (status) {
      'succeeded' => Icons.check,
      'failed' => Icons.close,
      'refunded' => Icons.undo,
      _ => Icons.schedule,
    };
