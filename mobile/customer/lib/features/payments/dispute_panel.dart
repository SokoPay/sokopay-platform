import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

/// Under a receipt for a payment to a business: the dispute status, or a
/// "Report a problem" button. Only shown when the server says the payment can be
/// disputed (the payer's own, completed, within the dispute window, not refunded).
class DisputePanel extends StatefulWidget {
  const DisputePanel({super.key, required this.reference});
  final String reference;
  @override
  State<DisputePanel> createState() => _DisputePanelState();
}

class _DisputePanelState extends State<DisputePanel> {
  late final ApiClient _api;
  Map<String, dynamic>? _receipt;

  static const reasons = {
    'not_received': "I didn't get what I paid for",
    'not_as_described': 'Not as described / faulty',
    'wrong_amount': 'I was charged the wrong amount',
    'duplicate': 'I was charged twice',
    'unauthorised': "I didn't make this payment",
    'other': 'Something else',
  };

  @override
  void initState() {
    super.initState();
    _api = context.read<ApiClient>();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await _api.get('/payments/${widget.reference}');
      if (mounted) setState(() => _receipt = Map<String, dynamic>.from(r.data));
    } catch (_) {/* the receipt above already shows any error */}
  }

  Future<void> _report() async {
    final result = await showModalBottomSheet<(String, String)>(
      context: context,
      isScrollControlled: true,
      builder: (_) => const _ReportSheet(reasons: reasons),
    );
    if (result == null || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await _api.post('/disputes',
          data: {'reference': widget.reference, 'reason': result.$1, 'description': result.$2});
      messenger.showSnackBar(const SnackBar(content: Text("We've told the business. We'll keep you posted.")));
      _load();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  Future<void> _withdraw(String id) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await _api.post('/disputes/$id/withdraw');
      _load();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _receipt;
    if (r == null || !r.containsKey('can_dispute')) return const SizedBox.shrink();
    final dispute = r['dispute'] as Map?;
    final active = dispute != null && (dispute['status'] == 'open' || dispute['status'] == 'responded');
    return Padding(
      padding: const EdgeInsets.only(top: 16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (dispute != null)
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: SokoColors.bg, borderRadius: BorderRadius.circular(10)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Dispute: ${dispute['status_display']}', style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              Text('${dispute['reason_display']} · ${dispute['amount_display']}'),
              if ((dispute['merchant_response'] ?? '').toString().isNotEmpty)
                Text('Business said: ${dispute['merchant_response']}', style: const TextStyle(fontSize: 13)),
              if ((dispute['decision_note'] ?? '').toString().isNotEmpty)
                Text('Outcome: ${dispute['decision_note']}', style: const TextStyle(fontSize: 13)),
              if (active)
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton(onPressed: () => _withdraw(dispute['id']), child: const Text('Withdraw')),
                ),
            ]),
          ),
        if (r['can_dispute'] == true)
          OutlinedButton.icon(
            onPressed: _report,
            icon: const Icon(Icons.report_problem_outlined),
            label: const Text('Report a problem with this payment'),
          ),
      ]),
    );
  }
}

class _ReportSheet extends StatefulWidget {
  const _ReportSheet({required this.reasons});
  final Map<String, String> reasons;
  @override
  State<_ReportSheet> createState() => _ReportSheetState();
}

class _ReportSheetState extends State<_ReportSheet> {
  String? _reason;
  final _text = TextEditingController();

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final ok = _reason != null && _text.text.trim().length >= 10;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const Text('What went wrong?', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          RadioGroup<String>(
            groupValue: _reason,
            onChanged: (v) => setState(() => _reason = v),
            child: Column(children: [
              for (final e in widget.reasons.entries)
                RadioListTile<String>(value: e.key, title: Text(e.value), dense: true, contentPadding: EdgeInsets.zero),
            ]),
          ),
          TextField(
            controller: _text,
            maxLines: 3,
            maxLength: 1000,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(hintText: 'Tell us what happened (at least 10 characters)'),
          ),
          const Text('The business gets 7 days to respond. If it isn\'t settled, SokoPay decides.',
              style: TextStyle(fontSize: 12, color: SokoColors.inkMuted)),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: ok ? () => Navigator.pop(context, (_reason!, _text.text.trim())) : null,
            child: const Text('Send'),
          ),
        ]),
      ),
    );
  }
}
