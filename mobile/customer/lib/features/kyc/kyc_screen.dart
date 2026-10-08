import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

import 'selfie_screen.dart';

/// Wallet limits and identity verification (DEMI requirement).
///   GET  /kyc           → tier, limits, usage, frozen
///   POST /kyc/upgrade   {ghana_card_number}
class KycScreen extends StatefulWidget {
  const KycScreen({super.key});
  @override
  State<KycScreen> createState() => _KycScreenState();
}

class _KycScreenState extends State<KycScreen> {
  late Future<Map<String, dynamic>> _future;
  final _card = TextEditingController(text: 'GHA-');
  bool _busy = false;
  String? _error;

  ApiClient get _api => context.read<ApiClient>();

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<Map<String, dynamic>> _load() async =>
      Map<String, dynamic>.from((await _api.get('/kyc')).data);

  Future<void> _upgrade() async {
    setState(() { _busy = true; _error = null; });
    try {
      await _api.post('/kyc/upgrade', data: {'ghana_card_number': _card.text.trim()});
      setState(() => _future = _load());
    } catch (e) {
      setState(() => _error = apiErrorMessage(e, fallback: 'Verification failed.'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Limits & verification')),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) return Center(child: Text(apiErrorMessage(snap.error!)));
          final k = snap.data!;
          final limits = Map<String, dynamic>.from(k['limits'] as Map);
          final usage = Map<String, dynamic>.from(k['usage'] as Map);
          final tier = k['tier'] as int;
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              if (k['frozen'] == true)
                Container(
                  padding: const EdgeInsets.all(12),
                  margin: const EdgeInsets.only(bottom: 12),
                  decoration: BoxDecoration(color: const Color(0xFFFDECEC),
                      borderRadius: BorderRadius.circular(8)),
                  child: const Text('Your wallet is on hold. Please contact SokoPay support.',
                      style: TextStyle(color: SokoColors.danger)),
                ),
              Text(k['tier_name'] ?? 'Tier $tier',
                  style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
              const SizedBox(height: 12),
              _row('Maximum balance', limits['max_balance']),
              _row('Per transaction', limits['max_txn']),
              _row('Sent today', '${usage['daily_out']} of ${limits['daily_out']}'),
              _row('Sent this month', '${usage['monthly_out']} of ${limits['monthly_out']}'),
              const Divider(height: 32),
              if (tier == 0) ...[
                const Text('Raise your limits',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                const SizedBox(height: 4),
                const Text('Verify your Ghana Card to move up a tier.',
                    style: TextStyle(color: Colors.black54)),
                const SizedBox(height: 12),
                TextField(
                  controller: _card,
                  textCapitalization: TextCapitalization.characters,
                  decoration: const InputDecoration(
                      labelText: 'Ghana Card number', hintText: 'GHA-123456789-0'),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 8),
                  Text(_error!, style: const TextStyle(color: SokoColors.danger)),
                ],
                const SizedBox(height: 12),
                ElevatedButton(
                  onPressed: _busy ? null : _upgrade,
                  child: _busy
                      ? const SizedBox(height: 20, width: 20,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Text('Verify'),
                ),
              ] else if (tier == 1) ...[
                const Text('Raise your limits further',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                const SizedBox(height: 4),
                const Text('A quick selfie check matches you to your Ghana Card photo.',
                    style: TextStyle(color: Colors.black54)),
                const SizedBox(height: 12),
                ElevatedButton.icon(
                  onPressed: () async {
                    final ok = await Navigator.of(context).push<bool>(
                        MaterialPageRoute(builder: (_) => const SelfieScreen()));
                    if (ok == true) setState(() => _future = _load());
                  },
                  icon: const Icon(Icons.camera_alt),
                  label: const Text('Take selfie'),
                ),
              ] else
                const Text('You have the highest verification level.',
                    style: TextStyle(color: SokoColors.success)),
            ],
          );
        },
      ),
    );
  }

  Widget _row(String label, Object? value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [Text(label), Text('$value', style: const TextStyle(fontWeight: FontWeight.w600))],
        ),
      );
}
