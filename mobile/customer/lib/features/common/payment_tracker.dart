import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../bills/bills_service.dart';

/// Shows a payment's outcome and, while it is still pending (waiting for the
/// customer to approve a MoMo prompt), polls the backend until it settles.
/// Shared by the bill, airtime and data flows.
class PaymentTracker extends StatefulWidget {
  const PaymentTracker({super.key, required this.initial, required this.service});
  final PaymentResult initial;
  final BillsService service;

  @override
  State<PaymentTracker> createState() => _PaymentTrackerState();
}

class _PaymentTrackerState extends State<PaymentTracker> {
  late PaymentResult _result = widget.initial;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    if (_result.status == 'pending') {
      _poll = Timer.periodic(const Duration(seconds: 3), (t) async {
        try {
          final next = await widget.service.status(_result.reference);
          if (!mounted) return;
          setState(() => _result = next);
          if (next.status != 'pending') t.cancel();
        } catch (_) {/* keep polling; transient network errors are expected */}
      });
    }
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final r = _result;
    final (icon, color, title) = switch (r.status) {
      'succeeded' => (Icons.check_circle, Colors.green, 'Payment successful'),
      'refunded' => (Icons.undo, Colors.blueGrey, 'Refunded'),
      'failed' => (Icons.cancel, Colors.red, 'Payment failed'),
      _ => (Icons.hourglass_top, Colors.orange, 'Approve on your phone'),
    };
    return Center(
      child: SingleChildScrollView(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, color: color, size: 64),
            const SizedBox(height: 16),
            Text(title, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text('GH₵ ${r.total}', style: const TextStyle(fontSize: 24)),
            const SizedBox(height: 4),
            Text('Ref ${r.reference}', style: const TextStyle(color: Colors.black45)),
            if (r.status == 'pending') ...[
              const SizedBox(height: 16),
              const Text('Check your phone for the mobile money prompt.',
                  textAlign: TextAlign.center, style: TextStyle(color: Colors.black54)),
            ],
            if (r.status == 'failed' || r.status == 'refunded') ...[
              const SizedBox(height: 16),
              const Text('If money left your account, it will be refunded automatically.',
                  textAlign: TextAlign.center, style: TextStyle(color: Colors.black54)),
            ],
            if (r.deliveryToken.isNotEmpty) ...[
              const SizedBox(height: 20),
              const Text('Your token', style: TextStyle(color: Colors.black54)),
              const SizedBox(height: 4),
              SelectableText(r.deliveryToken,
                  style: const TextStyle(fontSize: 22, letterSpacing: 2,
                      fontWeight: FontWeight.w600)),
              TextButton.icon(
                onPressed: () =>
                    Clipboard.setData(ClipboardData(text: r.deliveryToken)),
                icon: const Icon(Icons.copy, size: 18),
                label: const Text('Copy token'),
              ),
            ],
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: () => Navigator.of(context).popUntil((route) => route.isFirst),
              child: const Text('Done'),
            ),
          ],
        ),
      ),
    );
  }
}
