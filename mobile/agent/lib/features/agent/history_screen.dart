import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'agent_service.dart';

/// Full transaction history: kind chips (All / Cash in / Cash out / Top-ups), period
/// picker, a summary for the period (totals per kind + current float), newest first
/// with infinite scroll (stable server cursor paging), grouped by day. Each row shows
/// its effect on the float — cash-in spends float, cash-out and top-ups add to it.
class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key});
  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  static const _kinds = <String?, String>{
    null: 'All', 'cash_in': 'Cash in', 'cash_out': 'Cash out', 'topup': 'Top-ups',
  };
  static const _periods = <String?, String>{
    'today': 'Today', '7d': 'Last 7 days', '30d': 'Last 30 days', null: 'All time',
  };

  late final AgentService _service;
  final _scroll = ScrollController();
  final _items = <HistoryTxn>[];
  String? _kind;
  String? _period = 'today';
  String? _cursor;
  HistorySummary? _summary;
  bool _loading = false;
  bool _done = false;
  String? _error;
  int _generation = 0; // drops responses for a filter the user has since changed

  @override
  void initState() {
    super.initState();
    _service = AgentService(context.read<ApiClient>());
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
      final page = await _service.history(kind: _kind, period: _period, cursor: _cursor);
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
      appBar: AppBar(title: const Text('Transactions')),
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
                if (_loading)
                  const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator())),
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
                    child: Text('No transactions here yet.',
                        textAlign: TextAlign.center, style: TextStyle(color: Colors.black45)),
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
            children: _kinds.entries
                .map((e) => Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        label: Text(e.value),
                        selected: _kind == e.key,
                        onSelected: (_) {
                          setState(() => _kind = e.key);
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
    for (final t in _items) {
      final d = t.createdAt;
      final isToday = d.year == now.year && d.month == now.month && d.day == now.day;
      final day = isToday ? 'Today' : dayFmt.format(d);
      if (day != lastDay) {
        out.add(Padding(
          padding: const EdgeInsets.only(top: 16, bottom: 4),
          child: Text(day, style: const TextStyle(fontWeight: FontWeight.w600, color: SokoColors.inkMuted)),
        ));
        lastDay = day;
      }
      out.add(_TxnTile(txn: t, onTap: () => context.push('/history/${t.id}')));
    }
    return out;
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({required this.summary, required this.period});
  final HistorySummary summary;
  final String period;

  @override
  Widget build(BuildContext context) {
    Widget cell(String label, KindTotal t) => Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label, style: const TextStyle(color: Colors.white60, fontSize: 12)),
            const SizedBox(height: 2),
            Text(t.total, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700)),
            Text('${t.count} txn${t.count == 1 ? '' : 's'}',
                style: const TextStyle(color: Colors.white60, fontSize: 12)),
          ]),
        );
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: SokoColors.ink, borderRadius: BorderRadius.circular(12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('$period · Float now ${summary.float}', style: const TextStyle(color: Colors.white70)),
        const SizedBox(height: 12),
        Row(children: [
          cell('Cash in', summary.cashIn),
          cell('Cash out', summary.cashOut),
          cell('Top-ups', summary.topup),
        ]),
      ]),
    );
  }
}

class _TxnTile extends StatelessWidget {
  const _TxnTile({required this.txn, required this.onTap});
  final HistoryTxn txn;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final (icon, color) = kindStyle(txn.kind);
    final subtitle = [
      DateFormat('HH:mm').format(txn.createdAt),
      if (txn.customer.isNotEmpty) txn.customer,
    ].join(' · ');
    return ListTile(
      onTap: onTap,
      contentPadding: EdgeInsets.zero,
      leading: CircleAvatar(backgroundColor: color.withValues(alpha: 0.12), child: Icon(icon, color: color)),
      title: Text(txn.kindDisplay, style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text(subtitle),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text(txn.amountDisplay, style: const TextStyle(fontWeight: FontWeight.w600)),
          Text('Float ${txn.floatEffect}', style: const TextStyle(fontSize: 11, color: SokoColors.inkMuted)),
        ],
      ),
    );
  }
}

(IconData, Color) kindStyle(String kind) => switch (kind) {
      'cash_in' => (Icons.south_west, SokoColors.success),
      'cash_out' => (Icons.north_east, SokoColors.orange),
      _ => (Icons.add_card, SokoColors.ink),
    };
