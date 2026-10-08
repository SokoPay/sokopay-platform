import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

import 'wallet_service.dart';

/// Wallet home: balance, add-money and send actions, and recent activity.
class WalletScreen extends StatefulWidget {
  const WalletScreen({super.key});
  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> {
  late final WalletService _service;
  late Future<WalletState> _future;

  @override
  void initState() {
    super.initState();
    _service = WalletService(context.read<ApiClient>());
    _reload();
  }

  void _reload() => setState(() => _future = _service.load());

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Wallet'),
        actions: [
          IconButton(
            tooltip: 'Limits & verification',
            icon: const Icon(Icons.verified_user_outlined),
            onPressed: () => context.push('/kyc'),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async => _reload(),
        child: FutureBuilder<WalletState>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(children: const [
                Padding(
                  padding: EdgeInsets.all(24),
                  child: Text(
                      'Wallet is not available yet. It unlocks when SokoPay receives '
                      'its e-money (DEMI) licence.',
                      textAlign: TextAlign.center),
                )
              ]);
            }
            final w = snap.data!;
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Container(
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(
                    color: SokoColors.ink,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Wallet balance',
                          style: TextStyle(color: Colors.white70)),
                      const SizedBox(height: 6),
                      Text(w.balanceDisplay,
                          style: const TextStyle(
                              color: Colors.white,
                              fontSize: 30,
                              fontWeight: FontWeight.w700)),
                    ],
                  ),
                ),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: ElevatedButton.icon(
                        onPressed: () async {
                          await context.push('/wallet/fund');
                          _reload();
                        },
                        icon: const Icon(Icons.add),
                        label: const Text('Add money'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: ElevatedButton.icon(
                        style: ElevatedButton.styleFrom(
                            backgroundColor: SokoColors.ink),
                        onPressed: () async {
                          await context.push('/wallet/send');
                          _reload();
                        },
                        icon: const Icon(Icons.send),
                        label: const Text('Send'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 24),
                const Text('Activity', style: TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                if (w.activity.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(16),
                    child: Text('No wallet activity yet.',
                        style: TextStyle(color: Colors.black45)),
                  )
                else
                  ...w.activity.map((e) {
                    final isIn = e.direction == 'in';
                    return ListTile(
                      leading: Icon(isIn ? Icons.south_west : Icons.north_east,
                          color: isIn ? SokoColors.success : SokoColors.ink),
                      title: Text(e.narrative, maxLines: 1,
                          overflow: TextOverflow.ellipsis),
                      trailing: Text('${isIn ? '+' : '-'} ${e.amount}',
                          style: TextStyle(
                              color: isIn ? SokoColors.success : SokoColors.ink,
                              fontWeight: FontWeight.w600)),
                    );
                  }),
              ],
            );
          },
        ),
      ),
    );
  }
}
