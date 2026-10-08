import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:qr_flutter/qr_flutter.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../business/merchant_service.dart';

/// Shows a payment request's QR with a countdown, and polls every 3 seconds until the
/// customer has paid (then a clear "Paid" screen), the request expires, or the
/// cashier cancels. Polling stops when the screen closes.
class RequestQrScreen extends StatefulWidget {
  const RequestQrScreen({super.key, required this.token});
  final String token;
  @override
  State<RequestQrScreen> createState() => _RequestQrScreenState();
}

class _RequestQrScreenState extends State<RequestQrScreen> {
  late final MerchantService _service;
  PaymentRequestInfo? _req;
  String? _error;
  Timer? _poll;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _service = MerchantService(context.read<ApiClient>());
    _load();
    _poll = Timer.periodic(const Duration(seconds: 3), (_) => _load());
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {}); // refresh the countdown
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await _service.request(widget.token);
      if (!mounted) return;
      setState(() {
        _req = r;
        _error = null;
      });
      if (!r.isOpen) {
        _poll?.cancel();
        _tick?.cancel();
      }
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorMessage(e));
    }
  }

  Future<void> _cancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Cancel this request?'),
        content: const Text('The customer will no longer be able to pay with this QR.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep')),
          TextButton(onPressed: () => Navigator.pop(c, true), child: const Text('Cancel request')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await _service.cancelRequest(widget.token);
      await _load();
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  String _remaining(DateTime expires) {
    final d = expires.difference(DateTime.now());
    if (d.isNegative) return '0:00';
    return '${d.inMinutes}:${(d.inSeconds % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final r = _req;
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(title: const Text('Payment request')),
      body: r == null
          ? Center(child: _error == null ? const CircularProgressIndicator() : Text(_error!))
          : r.isPaid
              ? _Paid(req: r)
              : ListView(
                  padding: const EdgeInsets.all(24),
                  children: [
                    Text(r.amountDisplay ?? 'Customer enters the amount',
                        textAlign: TextAlign.center,
                        style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w700)),
                    if (r.description.isNotEmpty)
                      Text(r.description,
                          textAlign: TextAlign.center, style: const TextStyle(color: SokoColors.inkMuted)),
                    const SizedBox(height: 20),
                    Center(
                      child: Opacity(
                        opacity: r.isOpen ? 1 : 0.15,
                        child: QrImageView(data: r.payload, version: QrVersions.auto, size: 280),
                      ),
                    ),
                    const SizedBox(height: 16),
                    if (r.isOpen) ...[
                      const Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                        SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                        SizedBox(width: 10),
                        Text('Waiting for the customer to pay…'),
                      ]),
                      const SizedBox(height: 6),
                      Text('Expires in ${_remaining(r.expiresAt)}',
                          textAlign: TextAlign.center, style: const TextStyle(color: SokoColors.inkMuted)),
                      const SizedBox(height: 24),
                      OutlinedButton(onPressed: _cancel, child: const Text('Cancel request')),
                    ] else
                      Text(r.status == 'cancelled' ? 'This request was cancelled.' : 'This request has expired.',
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: SokoColors.danger, fontWeight: FontWeight.w600)),
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: SokoColors.warning)),
                    ],
                  ],
                ),
    );
  }
}

class _Paid extends StatelessWidget {
  const _Paid({required this.req});
  final PaymentRequestInfo req;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.check_circle, color: SokoColors.success, size: 96),
          const SizedBox(height: 16),
          const Text('Paid', style: TextStyle(fontSize: 28, fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          Text(req.amountDisplay ?? '', style: const TextStyle(fontSize: 22)),
          if (req.description.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(req.description, style: const TextStyle(color: SokoColors.inkMuted)),
          ],
          const SizedBox(height: 32),
          ElevatedButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Done')),
        ]),
      ),
    );
  }
}
