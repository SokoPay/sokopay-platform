import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../business/merchant_service.dart';
import '../settlements/pin_confirm.dart';

/// Customer disputes. While a dispute is open its amount is held from settlement.
/// Owner/Finance can refund (PIN) or explain; SokoPay decides if it isn't settled.
class DisputesScreen extends StatefulWidget {
  const DisputesScreen({super.key});
  @override
  State<DisputesScreen> createState() => _DisputesScreenState();
}

class _DisputesScreenState extends State<DisputesScreen> {
  late final MerchantService _service;
  bool _closed = false;
  late Future<DisputesPage> _page;

  @override
  void initState() {
    super.initState();
    _service = MerchantService(context.read<ApiClient>());
    _page = _service.disputes();
  }

  void _load() => setState(() => _page = _service.disputes(closed: _closed));

  Future<void> _refund(DisputeInfo d) async {
    final pin = await askForPin(context, action: 'refund ${d.amountDisplay}');
    if (pin == null || !mounted) return;
    await _answer(() => _service.respondToDispute(d.id, accept: true, pin: pin), 'Customer refunded');
  }

  Future<void> _explain(DisputeInfo d) async {
    final ctrl = TextEditingController();
    final text = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Explain your side'),
        content: TextField(controller: ctrl, maxLines: 4, maxLength: 1000,
            decoration: const InputDecoration(hintText: 'What happened? SokoPay will review it.')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, ctrl.text.trim()), child: const Text('Send')),
        ],
      ),
    );
    ctrl.dispose();
    if (text == null || text.isEmpty || !mounted) return;
    await _answer(() => _service.respondToDispute(d.id, accept: false, response: text), 'Sent to SokoPay');
  }

  Future<void> _answer(Future<void> Function() call, String done) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await call();
      messenger.showSnackBar(SnackBar(content: Text(done)));
      _load();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final date = DateFormat('d MMM yyyy');
    return Scaffold(
      appBar: AppBar(title: const Text('Disputes')),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: SegmentedButton<bool>(
            segments: const [
              ButtonSegment(value: false, label: Text('Active')),
              ButtonSegment(value: true, label: Text('Closed')),
            ],
            selected: {_closed},
            onSelectionChanged: (v) {
              _closed = v.first;
              _load();
            },
          ),
        ),
        Expanded(
          child: FutureBuilder<DisputesPage>(
            future: _page,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) {
                return const Center(child: CircularProgressIndicator());
              }
              if (snap.hasError) return Center(child: Text(apiErrorMessage(snap.error!)));
              final page = snap.data!;
              return RefreshIndicator(
                onRefresh: () async => _load(),
                child: ListView(padding: const EdgeInsets.all(16), children: [
                  if (!_closed && page.results.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Text('${page.heldDisplay} is held from settlement until these are resolved.',
                          style: const TextStyle(color: SokoColors.inkMuted)),
                    ),
                  if (page.results.isEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 48),
                      child: Text(_closed ? 'No closed disputes.' : 'No open disputes.', textAlign: TextAlign.center),
                    ),
                  for (final d in page.results)
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(14),
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Row(children: [
                            Expanded(child: Text(d.reasonDisplay, style: const TextStyle(fontWeight: FontWeight.w700))),
                            Text(d.amountDisplay, style: const TextStyle(fontWeight: FontWeight.w700)),
                          ]),
                          const SizedBox(height: 4),
                          Text('"${d.description}"'),
                          const SizedBox(height: 6),
                          Text('${d.customer} · ${d.statusDisplay}'
                              '${d.waitingForYou ? ' · answer by ${date.format(d.respondBy)}' : ''}',
                              style: const TextStyle(color: SokoColors.inkMuted, fontSize: 12)),
                          if (d.merchantResponse.isNotEmpty)
                            Padding(
                              padding: const EdgeInsets.only(top: 6),
                              child: Text('You said: ${d.merchantResponse}', style: const TextStyle(fontSize: 13)),
                            ),
                          if (d.decisionNote.isNotEmpty)
                            Padding(
                              padding: const EdgeInsets.only(top: 6),
                              child: Text('Outcome: ${d.decisionNote}', style: const TextStyle(fontSize: 13)),
                            ),
                          Row(children: [
                            TextButton(
                              onPressed: () => context.push('/payments/${d.paymentReference}'),
                              child: const Text('View payment'),
                            ),
                            const Spacer(),
                            if (d.waitingForYou && page.canRespond) ...[
                              TextButton(onPressed: () => _explain(d), child: const Text('Explain')),
                              FilledButton(onPressed: () => _refund(d), child: const Text('Refund')),
                            ],
                          ]),
                        ]),
                      ),
                    ),
                ]),
              );
            },
          ),
        ),
      ]),
    );
  }
}
