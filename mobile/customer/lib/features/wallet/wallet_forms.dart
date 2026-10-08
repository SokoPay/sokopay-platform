import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

import 'wallet_service.dart';
import '../common/saved_recipients.dart';

/// Add money to the wallet (MoMo top-up). The customer approves on their phone.
class WalletFundScreen extends StatefulWidget {
  const WalletFundScreen({super.key});
  @override
  State<WalletFundScreen> createState() => _WalletFundScreenState();
}

class _WalletFundScreenState extends State<WalletFundScreen> {
  final _amount = TextEditingController();
  String _network = 'mtn';
  bool _busy = false;
  String? _error;
  bool _done = false;

  Future<void> _submit() async {
    setState(() { _busy = true; _error = null; });
    try {
      await WalletService(context.read<ApiClient>())
          .fund(_amount.text.trim(), _network);
      setState(() => _done = true);
    } on DioException catch (e) {
      setState(() => _error = _msg(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Add money')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: _done
            ? const _Waiting()
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                    controller: _amount,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    decoration: const InputDecoration(
                        labelText: 'Amount (GH₵)', prefixText: 'GH₵ '),
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: _network,
                    decoration: const InputDecoration(labelText: 'Pay from'),
                    items: const [
                      DropdownMenuItem(value: 'mtn', child: Text('MTN MoMo')),
                      DropdownMenuItem(value: 'telecel', child: Text('Telecel Cash')),
                      DropdownMenuItem(value: 'at', child: Text('AT Money')),
                    ],
                    onChanged: (v) => setState(() => _network = v!),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(_error!, style: const TextStyle(color: Colors.red)),
                  ],
                  const Spacer(),
                  ElevatedButton(
                    onPressed: _busy ? null : _submit,
                    child: _busy ? const _Spinner() : const Text('Add money'),
                  ),
                ],
              ),
      ),
    );
  }
}

/// Send money to another SokoPay wallet (P2P).
class WalletSendScreen extends StatefulWidget {
  const WalletSendScreen({super.key});
  @override
  State<WalletSendScreen> createState() => _WalletSendScreenState();
}

class _WalletSendScreenState extends State<WalletSendScreen> {
  final _phone = TextEditingController();
  final _amount = TextEditingController();
  bool _busy = false;
  String? _error;
  String? _success;
  String? _name; // confirmed recipient name, e.g. "Ama M."

  Future<void> _check() async {
    setState(() { _busy = true; _error = null; _name = null; });
    try {
      final n = await WalletService(context.read<ApiClient>()).lookup(_phone.text.trim());
      setState(() {
        _name = n;
        if (n == null) _error = 'No SokoPay account for that number or wallet ID.';
      });
    } on DioException catch (e) {
      setState(() => _error = _msg(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _submit() async {
    setState(() { _busy = true; _error = null; });
    try {
      final r = await WalletService(context.read<ApiClient>())
          .send(_phone.text.trim(), _amount.text.trim());
      setState(() => _success = 'Sent ${r['amount']} to ${r['recipient']}');
    } on DioException catch (e) {
      setState(() => _error = _msg(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Send money')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: _success != null
            ? Center(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.check_circle, color: Colors.green, size: 64),
                    const SizedBox(height: 16),
                    Text(_success!, textAlign: TextAlign.center,
                        style: const TextStyle(fontSize: 18)),
                    const SizedBox(height: 12),
                    SaveRecipientButton(kind: 'sokopay', value: _phone.text.trim()),
                    const SizedBox(height: 12),
                    ElevatedButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: const Text('Done'),
                    ),
                  ],
                ),
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                    controller: _phone,
                    keyboardType: TextInputType.phone,
                    decoration: InputDecoration(
                      labelText: 'Phone number or SokoPay wallet ID',
                      hintText: '0244058519 or 7XXX XXX XXX',
                      prefixIcon: IconButton(
                        tooltip: 'Saved recipients',
                        icon: const Icon(Icons.star_border, color: SokoColors.orange),
                        onPressed: () async {
                          final r = await pickSavedRecipient(context, kind: 'sokopay');
                          if (r == null) return;
                          _phone.text = r['value'];
                          _check();
                        },
                      ),
                      suffixIcon: TextButton(onPressed: _busy ? null : _check, child: const Text('Check')),
                    ),
                    onChanged: (_) => setState(() => _name = null),
                  ),
                  if (_name != null)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.verified_user, color: Colors.green),
                      title: Text('Sending to $_name'),
                    ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _amount,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    decoration: const InputDecoration(
                        labelText: 'Amount (GH₵)', prefixText: 'GH₵ '),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(_error!, style: const TextStyle(color: Colors.red)),
                  ],
                  const Spacer(),
                  ElevatedButton(
                    // Only after the recipient's name has been confirmed.
                    onPressed: _busy || _name == null ? null : _submit,
                    child: _busy ? const _Spinner() : const Text('Send'),
                  ),
                ],
              ),
      ),
    );
  }
}

String _msg(DioException e) => e.response?.data is Map
    ? (e.response?.data['error']?.toString() ?? 'Something went wrong')
    : 'Something went wrong';

class _Spinner extends StatelessWidget {
  const _Spinner();
  @override
  Widget build(BuildContext context) => const SizedBox(
      height: 20, width: 20,
      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white));
}

class _Waiting extends StatelessWidget {
  const _Waiting();
  @override
  Widget build(BuildContext context) => Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.hourglass_top, color: Colors.orange, size: 64),
            const SizedBox(height: 16),
            const Text('Approve on your phone',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            const Text('Your wallet is credited once you approve the mobile money prompt.',
                textAlign: TextAlign.center, style: TextStyle(color: Colors.black54)),
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Done'),
            ),
          ],
        ),
      );
}
