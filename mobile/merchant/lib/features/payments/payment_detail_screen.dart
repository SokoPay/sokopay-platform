import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../business/merchant_service.dart';
import '../settlements/pin_confirm.dart';
import 'payments_screen.dart';

/// One payment, fetched by reference (so a push notification can open it directly).
/// Fee and net are shown only to roles allowed to see money; the payer's number is
/// always masked by the server.
class PaymentDetailScreen extends StatefulWidget {
  const PaymentDetailScreen({super.key, required this.reference});
  final String reference;
  @override
  State<PaymentDetailScreen> createState() => _PaymentDetailScreenState();
}

class _PaymentDetailScreenState extends State<PaymentDetailScreen> {
  late final MerchantService _service;
  late Future<PaymentInfo> _payment;

  @override
  void initState() {
    super.initState();
    _service = MerchantService(context.read<ApiClient>());
    _payment = _service.payment(widget.reference);
  }

  void _reload() => setState(() => _payment = _service.payment(widget.reference));

  Future<void> _refund(PaymentInfo p) async {
    final form = await showDialog<(String, String)>(context: context, builder: (_) => _RefundDialog(max: p.refundableDisplay ?? ''));
    if (form == null || !mounted) return;
    final pin = await askForPin(context, action: form.$1.isEmpty ? 'refund ${p.refundableDisplay}' : 'refund GH₵ ${form.$1}');
    if (pin == null || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await _service.refund(p.reference, amount: form.$1, reason: form.$2, pin: pin);
      messenger.showSnackBar(const SnackBar(content: Text('Refund sent to the customer')));
      _reload();
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final when = DateFormat('d MMM yyyy, HH:mm');
    return Scaffold(
      appBar: AppBar(title: const Text('Payment')),
      body: FutureBuilder<PaymentInfo>(
        future: _payment,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(apiErrorMessage(snap.error!))));
          }
          final p = snap.data!;
          final color = paymentStatusColor(p.status);
          return ListView(padding: const EdgeInsets.all(20), children: [
            Center(
              child: CircleAvatar(
                radius: 32,
                backgroundColor: color.withValues(alpha: 0.12),
                child: Icon(paymentStatusIcon(p.status), color: color, size: 32),
              ),
            ),
            const SizedBox(height: 12),
            Text(p.amountDisplay,
                textAlign: TextAlign.center, style: const TextStyle(fontSize: 32, fontWeight: FontWeight.w700)),
            Text(p.statusDisplay,
                textAlign: TextAlign.center, style: TextStyle(color: color, fontWeight: FontWeight.w600)),
            if (p.failure != null) ...[
              const SizedBox(height: 4),
              Text(p.failure!, textAlign: TextAlign.center, style: const TextStyle(color: SokoColors.inkMuted)),
            ],
            if (p.isTest)
              const Padding(
                padding: EdgeInsets.only(top: 6),
                child: Text('Test payment — no real money', textAlign: TextAlign.center,
                    style: TextStyle(color: SokoColors.warning)),
              ),
            const SizedBox(height: 24),
            if (p.note.isNotEmpty) _Row('For', p.note),
            _Row('Paid with', p.method),
            _Row('Customer', p.payer),
            _Row('Started', when.format(p.createdAt)),
            if (p.completedAt != null) _Row('Completed', when.format(p.completedAt!)),
            if (p.feeDisplay != null) ...[
              const Divider(height: 32),
              _Row('Amount', p.amountDisplay),
              _Row('SokoPay fee', '− ${p.feeDisplay}'),
              _Row('You receive', p.netDisplay ?? '', bold: true),
            ],
            const Divider(height: 32),
            _Row('Reference', p.reference, copyable: true),
            if (p.dispute != null) ...[
              const SizedBox(height: 16),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: SokoColors.warning.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(10)),
                child: Text('Customer dispute: ${p.dispute!.reasonDisplay} (${p.dispute!.amountDisplay}). '
                    '${p.dispute!.waitingForYou ? 'Answer it in Disputes.' : p.dispute!.statusDisplay}'),
              ),
            ],
            if (p.refunds.isNotEmpty) ...[
              const Divider(height: 32),
              const Text('Refunds', style: TextStyle(fontWeight: FontWeight.w700)),
              for (final r in p.refunds)
                _Row('${when.format(r.createdAt)} · ${r.statusDisplay}', r.amountDisplay),
            ],
            if (p.canRefund) ...[
              const SizedBox(height: 20),
              OutlinedButton.icon(
                onPressed: () => _refund(p),
                icon: const Icon(Icons.undo),
                label: Text('Refund (up to ${p.refundableDisplay})'),
              ),
            ],
          ]);
        },
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row(this.label, this.value, {this.bold = false, this.copyable = false});
  final String label;
  final String value;
  final bool bold;
  final bool copyable;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(children: [
        Expanded(child: Text(label, style: const TextStyle(color: SokoColors.inkMuted))),
        Flexible(
          child: GestureDetector(
            onTap: copyable
                ? () {
                    Clipboard.setData(ClipboardData(text: value));
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied')));
                  }
                : null,
            child: Text(value,
                textAlign: TextAlign.right,
                style: TextStyle(fontWeight: bold ? FontWeight.w700 : FontWeight.w500,
                    fontFamily: copyable ? 'monospace' : null)),
          ),
        ),
      ]),
    );
  }
}

/// Amount (blank = everything still refundable) and a reason the customer will see.
class _RefundDialog extends StatefulWidget {
  const _RefundDialog({required this.max});
  final String max;
  @override
  State<_RefundDialog> createState() => _RefundDialogState();
}

class _RefundDialogState extends State<_RefundDialog> {
  final _amount = TextEditingController();
  final _reason = TextEditingController();

  @override
  void dispose() {
    _amount.dispose();
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Refund customer'),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        TextField(
          controller: _amount,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: InputDecoration(labelText: 'Amount (GH₵)', helperText: 'Leave blank to refund ${widget.max}'),
        ),
        TextField(controller: _reason, decoration: const InputDecoration(labelText: 'Reason (the customer sees this)')),
        const SizedBox(height: 8),
        const Text('The money goes back the way the customer paid. Your SokoPay fee is not returned.',
            style: TextStyle(fontSize: 12, color: SokoColors.inkMuted)),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        FilledButton(
          onPressed: () {
            if (_reason.text.trim().isEmpty) return;
            Navigator.pop(context, (_amount.text.trim(), _reason.text.trim()));
          },
          child: const Text('Continue'),
        ),
      ],
    );
  }
}
