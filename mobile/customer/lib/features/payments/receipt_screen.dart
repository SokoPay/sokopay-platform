import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../bills/bills_service.dart';
import '../common/payment_tracker.dart';
import 'dispute_panel.dart';

/// A payment's receipt, opened by reference (from a notification or the inbox).
/// Reuses PaymentTracker, so a payment that is still pending keeps updating live and
/// an ECG token is shown with its copy button. The server only returns the caller's
/// own payments.
class ReceiptScreen extends StatefulWidget {
  const ReceiptScreen({super.key, required this.reference});
  final String reference;
  @override
  State<ReceiptScreen> createState() => _ReceiptScreenState();
}

class _ReceiptScreenState extends State<ReceiptScreen> {
  late final BillsService _service;
  late Future<PaymentResult> _payment;

  @override
  void initState() {
    super.initState();
    _service = BillsService(context.read<ApiClient>());
    _payment = _service.status(widget.reference);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Payment')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: FutureBuilder<PaymentResult>(
          future: _payment,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return Center(
                child: Text(apiErrorMessage(snap.error!, fallback: "We couldn't find that payment."),
                    textAlign: TextAlign.center),
              );
            }
            return Column(children: [
              Expanded(child: PaymentTracker(initial: snap.data!, service: _service)),
              DisputePanel(reference: widget.reference),
            ]);
          },
        ),
      ),
    );
  }
}
