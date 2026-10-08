import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../business/merchant_service.dart';

/// Create a one-sale QR with the amount locked in (or open, for the customer to type),
/// plus the list of recent requests and their status.
class RequestPaymentScreen extends StatefulWidget {
  const RequestPaymentScreen({super.key});
  @override
  State<RequestPaymentScreen> createState() => _RequestPaymentScreenState();
}

class _RequestPaymentScreenState extends State<RequestPaymentScreen> {
  final _form = GlobalKey<FormState>();
  final _amount = TextEditingController();
  final _description = TextEditingController();
  late final MerchantService _service;
  late Future<List<PaymentRequestInfo>> _recent;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _service = MerchantService(context.read<ApiClient>());
    _recent = _service.recentRequests();
  }

  @override
  void dispose() {
    _amount.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    if (!_form.currentState!.validate()) return;
    setState(() => _busy = true);
    try {
      final req = await _service.createRequest(
        amount: _amount.text.trim(),
        description: _description.text.trim(),
      );
      if (!mounted) return;
      await context.push('/request/${req.token}');
      _amount.clear();
      _description.clear();
      setState(() => _recent = _service.recentRequests());
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Request payment')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Form(
            key: _form,
            child: Column(children: [
              TextFormField(
                controller: _amount,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'^\d*\.?\d{0,2}'))],
                decoration: const InputDecoration(
                  labelText: 'Amount (GH₵)',
                  hintText: 'Leave blank to let the customer enter it',
                  prefixText: 'GH₵ ',
                ),
                validator: (v) {
                  final t = (v ?? '').trim();
                  if (t.isEmpty) return null;
                  final n = double.tryParse(t); // display check only; server parses exactly
                  if (n == null || n <= 0) return 'Enter an amount above zero';
                  return null;
                },
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _description,
                maxLength: 120,
                decoration: const InputDecoration(labelText: 'What for (shown to the customer)'),
              ),
              const SizedBox(height: 8),
              ElevatedButton.icon(
                onPressed: _busy ? null : _create,
                icon: _busy
                    ? const SizedBox(
                        width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Icon(Icons.qr_code_2),
                label: const Text('Show QR'),
              ),
              const SizedBox(height: 6),
              const Text('The QR is valid for 15 minutes.',
                  style: TextStyle(color: SokoColors.inkMuted, fontSize: 12)),
            ]),
          ),
          const SizedBox(height: 24),
          const Text('Recent requests', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          FutureBuilder<List<PaymentRequestInfo>>(
            future: _recent,
            builder: (context, snap) {
              final items = snap.data ?? [];
              if (snap.connectionState != ConnectionState.done) {
                return const Padding(padding: EdgeInsets.all(16), child: LinearProgressIndicator());
              }
              if (items.isEmpty) {
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: Text('No payment requests yet.', style: TextStyle(color: Colors.black45)),
                );
              }
              return Column(
                children: items
                    .map((r) => ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(r.amountDisplay ?? 'Open amount'),
                          subtitle: Text(r.description.isEmpty ? '—' : r.description),
                          trailing: RequestStatusChip(status: r.status),
                          onTap: r.isOpen ? () => context.push('/request/${r.token}') : null,
                        ))
                    .toList(),
              );
            },
          ),
        ],
      ),
    );
  }
}

class RequestStatusChip extends StatelessWidget {
  const RequestStatusChip({super.key, required this.status});
  final String status;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (status) {
      'paid' => ('Paid', SokoColors.success),
      'open' => ('Waiting', SokoColors.warning),
      'cancelled' => ('Cancelled', SokoColors.inkMuted),
      _ => ('Expired', SokoColors.inkMuted),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
      child: Text(label, style: TextStyle(color: color, fontWeight: FontWeight.w600, fontSize: 12)),
    );
  }
}
