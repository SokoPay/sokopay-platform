import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'agent_service.dart';

/// Everything the agent has earned, per transaction.
///   Cash-in, airtime, data and bill commission → "Redeem" moves it into the float any time.
///   Cash-out commission → SokoPay pays it into the float every two weeks.
class EarningsScreen extends StatefulWidget {
  const EarningsScreen({super.key});
  @override
  State<EarningsScreen> createState() => _EarningsScreenState();
}

class _EarningsScreenState extends State<EarningsScreen> {
  late final AgentService _service;
  Earnings? _top;
  final _rows = <EarningRow>[];
  String? _cursor;
  bool _loading = false;
  bool _done = false;
  bool _redeeming = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _service = AgentService(context.read<ApiClient>());
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _rows.clear();
      _cursor = null;
      _done = false;
      _error = null;
    });
    await _more();
  }

  Future<void> _more() async {
    if (_loading || _done) return;
    setState(() => _loading = true);
    try {
      final e = await _service.earnings(cursor: _cursor);
      if (!mounted) return;
      setState(() {
        if (_cursor == null) _top = e;
        _rows.addAll(e.results);
        _cursor = e.nextCursor;
        _done = e.nextCursor == null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _redeem() async {
    final top = _top;
    if (top == null || top.redeemableMinor <= 0) return;
    final pin = await askPinFor(context, action: 'Move ${top.redeemable} into your float');
    if (pin == null || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _redeeming = true);
    try {
      final amount = await _service.redeem(pin);
      messenger.showSnackBar(SnackBar(content: Text('$amount added to your float.')));
      await _reload();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    } finally {
      if (mounted) setState(() => _redeeming = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final top = _top;
    return Scaffold(
      appBar: AppBar(title: const Text('Earnings')),
      body: RefreshIndicator(
        onRefresh: _reload,
        child: NotificationListener<ScrollNotification>(
          onNotification: (n) {
            if (n.metrics.pixels > n.metrics.maxScrollExtent - 300) _more();
            return false;
          },
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.all(16),
            children: [
              if (top != null) ...[
                Container(
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(color: SokoColors.ink, borderRadius: BorderRadius.circular(12)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Text('Ready to redeem', style: TextStyle(color: Colors.white70)),
                    const SizedBox(height: 4),
                    Text(top.redeemable,
                        style: const TextStyle(color: Colors.white, fontSize: 30, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 4),
                    const Text('From cash-in, airtime, data and bill payments.',
                        style: TextStyle(color: Colors.white60, fontSize: 12)),
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: ElevatedButton(
                        style: ElevatedButton.styleFrom(backgroundColor: SokoColors.orange),
                        onPressed: top.redeemableMinor > 0 && !_redeeming ? _redeem : null,
                        child: _redeeming
                            ? const SizedBox(
                                height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                            : const Text('Redeem into my float'),
                      ),
                    ),
                  ]),
                ),
                const SizedBox(height: 12),
                Row(children: [
                  Expanded(
                      child: _Tile(
                          label: 'Cash-out earnings',
                          value: top.awaitingPayout,
                          note: top.nextPayoutDue == null
                              ? 'Paid by SokoPay every 2 weeks'
                              : 'Next payment around ${DateFormat('d MMM').format(top.nextPayoutDue!)}')),
                  const SizedBox(width: 12),
                  Expanded(child: _Tile(label: 'Paid into float so far', value: top.paid, note: 'Redeemed and paid')),
                ]),
                const SizedBox(height: 20),
                const Text('Per transaction', style: TextStyle(fontWeight: FontWeight.w600)),
              ],
              for (final r in _rows)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text('${r.productDisplay} · ${r.transactionAmount}'),
                  subtitle: Text('${DateFormat('d MMM yyyy, HH:mm').format(r.createdAt)} · ${r.reference}'),
                  trailing: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(r.amount, style: const TextStyle(fontWeight: FontWeight.w600, color: SokoColors.success)),
                      Text(
                          switch (r.status) {
                            'paid' => 'In your float',
                            'awaiting_payout' => 'Fortnightly payout',
                            _ => 'Redeemable',
                          },
                          style: const TextStyle(fontSize: 11, color: SokoColors.inkMuted)),
                    ],
                  ),
                ),
              if (_loading) const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator())),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(children: [
                    Text(_error!, textAlign: TextAlign.center),
                    TextButton(onPressed: _more, child: const Text('Try again')),
                  ]),
                ),
              if (!_loading && _error == null && _rows.isEmpty)
                const Padding(
                  padding: EdgeInsets.all(32),
                  child: Text('No earnings yet. You earn on every cash-in, cash-out and sale.',
                      textAlign: TextAlign.center, style: TextStyle(color: Colors.black45)),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Tile extends StatelessWidget {
  const _Tile({required this.label, required this.value, required this.note});
  final String label;
  final String value;
  final String note;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: SokoColors.surface, borderRadius: BorderRadius.circular(12)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: const TextStyle(fontSize: 12, color: SokoColors.inkMuted)),
          const SizedBox(height: 4),
          Text(value, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          const SizedBox(height: 2),
          Text(note, style: const TextStyle(fontSize: 11, color: SokoColors.inkMuted)),
        ]),
      );
}
