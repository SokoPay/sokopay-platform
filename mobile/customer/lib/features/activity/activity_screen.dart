import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'activity_service.dart';

/// Everything that moved the customer's money, in one list: bills, airtime, data, shop
/// payments, transfers, money sent/received, cash in/out, top-ups, remittances, loans.
/// Filter chips + period picker, a money in / money out summary, newest first with
/// infinite scroll (stable server cursor paging), grouped by day. Payments open their
/// receipt; everything else opens a details sheet.
class ActivityScreen extends StatefulWidget {
  const ActivityScreen({super.key, this.initialFilter});

  /// 'payments' | 'transfers' | 'wallet' — e.g. "See all" from Recently paid.
  final String? initialFilter;
  @override
  State<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends State<ActivityScreen> {
  static const _filters = <String?, String>{
    null: 'All', 'payments': 'Payments', 'transfers': 'Transfers', 'wallet': 'Wallet',
  };
  static const _periods = <String?, String>{
    '30d': 'Last 30 days', '7d': 'Last 7 days', 'today': 'Today', null: 'All time',
  };

  late final ActivityService _service;
  final _scroll = ScrollController();
  final _items = <ActivityItem>[];
  late String? _filter = const {'payments', 'transfers', 'wallet'}.contains(widget.initialFilter)
      ? widget.initialFilter
      : null;
  String? _period = '30d';
  String? _cursor;
  String? _moneyIn;
  String? _moneyOut;
  bool _loading = false;
  bool _done = false;
  String? _error;
  int _generation = 0; // drops responses for filters the user has since changed

  @override
  void initState() {
    super.initState();
    _service = ActivityService(context.read<ApiClient>());
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
      _moneyIn = _moneyOut = null;
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
      final page = await _service.page(filter: _filter, period: _period, cursor: _cursor);
      if (!mounted || gen != _generation) return;
      setState(() {
        _items.addAll(page.results);
        if (_cursor == null) {
          _moneyIn = page.moneyIn;
          _moneyOut = page.moneyOut;
        }
        _cursor = page.nextCursor;
        _done = page.nextCursor == null;
      });
    } catch (e) {
      if (mounted && gen == _generation) setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted && gen == _generation) setState(() => _loading = false);
    }
  }

  void _open(ActivityItem item) {
    if (item.source == 'payment' && item.reference.isNotEmpty) {
      context.push('/payments/${item.reference}');
      return;
    }
    showModalBottomSheet(context: context, builder: (_) => _DetailSheet(item: item));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Activity')),
      body: Column(children: [
        _filterBar(),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _reload,
            child: ListView(
              controller: _scroll,
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
              children: [
                if (_moneyIn != null) _SummaryCard(moneyIn: _moneyIn!, moneyOut: _moneyOut ?? '', period: _periods[_period]!),
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
                    child: Text('Nothing here yet.', textAlign: TextAlign.center, style: TextStyle(color: Colors.black45)),
                  ),
              ],
            ),
          ),
        ),
      ]),
    );
  }

  Widget _filterBar() {
    return Container(
      color: SokoColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: _filters.entries
                .map((e) => Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        label: Text(e.value),
                        selected: _filter == e.key,
                        onSelected: (_) {
                          setState(() => _filter = e.key);
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
    for (final item in _items) {
      final d = item.createdAt;
      final isToday = d.year == now.year && d.month == now.month && d.day == now.day;
      final day = isToday ? 'Today' : dayFmt.format(d);
      if (day != lastDay) {
        out.add(Padding(
          padding: const EdgeInsets.only(top: 16, bottom: 4),
          child: Text(day, style: const TextStyle(fontWeight: FontWeight.w600, color: SokoColors.inkMuted)),
        ));
        lastDay = day;
      }
      out.add(_ActivityTile(item: item, onTap: () => _open(item)));
    }
    return out;
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({required this.moneyIn, required this.moneyOut, required this.period});
  final String moneyIn;
  final String moneyOut;
  final String period;

  @override
  Widget build(BuildContext context) {
    Widget cell(String label, String value) => Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label, style: const TextStyle(color: Colors.white60)),
            const SizedBox(height: 2),
            Text(value, style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w700)),
          ]),
        );
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: SokoColors.ink, borderRadius: BorderRadius.circular(12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(period, style: const TextStyle(color: Colors.white70)),
        const SizedBox(height: 10),
        Row(children: [cell('Money in', moneyIn), cell('Money out', moneyOut)]),
      ]),
    );
  }
}

class _ActivityTile extends StatelessWidget {
  const _ActivityTile({required this.item, required this.onTap});
  final ActivityItem item;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final icon = categoryIcon(item.category);
    final amountColor = item.status == 'failed' || item.status == 'refunded'
        ? SokoColors.inkMuted
        : (item.isIn ? SokoColors.success : SokoColors.ink);
    return ListTile(
      onTap: onTap,
      contentPadding: EdgeInsets.zero,
      leading: CircleAvatar(
        backgroundColor: (item.isIn ? SokoColors.success : SokoColors.orange).withValues(alpha: 0.12),
        child: Icon(icon, color: item.isIn ? SokoColors.success : SokoColors.orange),
      ),
      title: Text(item.title, maxLines: 1, overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text('${DateFormat('HH:mm').format(item.createdAt)} · ${item.subtitle}',
          maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text(item.amountDisplay,
              style: TextStyle(
                fontWeight: FontWeight.w600,
                color: amountColor,
                decoration: item.status == 'failed' ? TextDecoration.lineThrough : null,
              )),
          if (item.status != 'succeeded') StatusLabel(status: item.status),
        ],
      ),
    );
  }
}

class StatusLabel extends StatelessWidget {
  const StatusLabel({super.key, required this.status});
  final String status;
  @override
  Widget build(BuildContext context) {
    final (text, color) = switch (status) {
      'pending' => ('Pending', SokoColors.warning),
      'failed' => ('Failed', SokoColors.danger),
      'refunded' => ('Refunded', SokoColors.inkMuted),
      _ => ('Done', SokoColors.success),
    };
    return Text(text, style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.w600));
  }
}

IconData categoryIcon(String category) => switch (category) {
      'bill' => Icons.receipt_long,
      'airtime' => Icons.phone_android,
      'data' => Icons.wifi,
      'shop' => Icons.storefront,
      'topup' => Icons.add_circle_outline,
      'transfer' || 'sent' => Icons.north_east,
      'received' || 'remittance' || 'salary' => Icons.south_west,
      'cash_in' => Icons.payments_outlined,
      'cash_out' => Icons.local_atm,
      'loan' => Icons.account_balance,
      'premium' => Icons.health_and_safety_outlined,
      _ => Icons.swap_horiz,
    };

/// Details for items without a receipt screen (transfers and wallet movements).
class _DetailSheet extends StatelessWidget {
  const _DetailSheet({required this.item});
  final ActivityItem item;

  @override
  Widget build(BuildContext context) {
    Widget row(String label, String value, {bool copy = false}) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(children: [
            Expanded(child: Text(label, style: const TextStyle(color: SokoColors.inkMuted))),
            GestureDetector(
              onTap: copy
                  ? () {
                      Clipboard.setData(ClipboardData(text: value));
                      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied')));
                    }
                  : null,
              child: Text(value, style: TextStyle(fontWeight: FontWeight.w600, fontFamily: copy ? 'monospace' : null)),
            ),
          ]),
        );
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(item.amountDisplay, textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700)),
          Text(item.title, textAlign: TextAlign.center, style: const TextStyle(fontSize: 16)),
          const SizedBox(height: 4),
          Center(child: StatusLabel(status: item.status)),
          if (item.status == 'refunded' && item.source == 'transfer')
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child: Text("This transfer didn't go through — the money is back in your wallet.",
                  textAlign: TextAlign.center, style: TextStyle(color: SokoColors.inkMuted)),
            ),
          const SizedBox(height: 16),
          row('Via', item.subtitle),
          row('When', DateFormat('d MMM yyyy, HH:mm').format(item.createdAt)),
          if (item.reference.isNotEmpty) row('Reference', item.reference, copy: true),
        ]),
      ),
    );
  }
}
