import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'merchant_service.dart';

/// Business home: balance (for roles that may see money) and the three actions —
/// show the counter QR, request a specific payment, settlements.
class BusinessHomeScreen extends StatefulWidget {
  const BusinessHomeScreen({super.key});
  @override
  State<BusinessHomeScreen> createState() => _BusinessHomeScreenState();
}

class _BusinessHomeScreenState extends State<BusinessHomeScreen> {
  late final MerchantService _service;
  late Future<BusinessProfile> _profile;

  @override
  void initState() {
    super.initState();
    _service = MerchantService(context.read<ApiClient>());
    _profile = _service.me();
  }

  Future<void> _refresh() async {
    setState(() => _profile = _service.me());
    await _profile.catchError((_) => BusinessProfile(
        name: '', isLive: false, role: '', canViewMoney: false, canMoveMoney: false));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('SokoPay Business'),
        actions: [
          IconButton(
            tooltip: 'Security',
            onPressed: () => context.push('/security'),
            icon: const Icon(Icons.lock_outline),
          ),
          IconButton(
            tooltip: 'Sign out',
            onPressed: () => context.read<AuthController>().signOut(),
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: FutureBuilder<BusinessProfile>(
          future: _profile,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(padding: const EdgeInsets.all(24), children: [
                const SizedBox(height: 80),
                const Icon(Icons.storefront_outlined, size: 48, color: SokoColors.inkMuted),
                const SizedBox(height: 12),
                Text(apiErrorMessage(snap.error!,
                        fallback: "This account isn't linked to a business yet."),
                    textAlign: TextAlign.center),
                const SizedBox(height: 16),
                OutlinedButton(onPressed: _refresh, child: const Text('Try again')),
              ]);
            }
            final p = snap.data!;
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _BalanceCard(profile: p),
                if (!p.isLive) ...[
                  const SizedBox(height: 12),
                  const _Notice(
                      'Your business is under review. QR payments switch on once SokoPay approves it.'),
                ],
                const SizedBox(height: 16),
                Row(children: [
                  Expanded(
                    child: _ActionCard(
                      icon: Icons.qr_code_2,
                      label: 'Counter QR',
                      enabled: p.isLive,
                      onTap: () => context.push('/qr'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _ActionCard(
                      icon: Icons.point_of_sale,
                      label: 'Request payment',
                      enabled: p.isLive,
                      onTap: () => context.push('/request'),
                    ),
                  ),
                ]),
                const SizedBox(height: 12),
                _ActionCard(
                  icon: Icons.receipt_long_outlined,
                  label: 'Payments',
                  enabled: true,
                  onTap: () => context.push('/payments'),
                ),
                if (p.canViewMoney) ...[
                  const SizedBox(height: 12),
                  _ActionCard(
                    icon: Icons.account_balance_wallet_outlined,
                    label: 'Settlements',
                    enabled: true,
                    onTap: () async {
                      await context.push('/settlements');
                      _refresh();
                    },
                  ),
                  const SizedBox(height: 12),
                  _ActionCard(
                    icon: Icons.gavel_outlined,
                    label: 'Disputes',
                    enabled: true,
                    onTap: () => context.push('/disputes'),
                  ),
                  const SizedBox(height: 12),
                  _ActionCard(
                    icon: Icons.description_outlined,
                    label: 'Statement',
                    enabled: true,
                    onTap: () => context.push('/statement'),
                  ),
                ],
              ],
            );
          },
        ),
      ),
    );
  }
}

class _BalanceCard extends StatelessWidget {
  const _BalanceCard({required this.profile});
  final BusinessProfile profile;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(color: SokoColors.ink, borderRadius: BorderRadius.circular(12)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(profile.name,
              style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(profile.role.isEmpty ? '' : '${profile.role[0].toUpperCase()}${profile.role.substring(1)}',
              style: const TextStyle(color: Colors.white60)),
          if (profile.canViewMoney) ...[
            const SizedBox(height: 16),
            const Text('Available to settle', style: TextStyle(color: Colors.white70)),
            const SizedBox(height: 4),
            Text(profile.availableDisplay ?? '—',
                style: const TextStyle(color: Colors.white, fontSize: 30, fontWeight: FontWeight.w700)),
          ],
        ],
      ),
    );
  }
}

class _ActionCard extends StatelessWidget {
  const _ActionCard({required this.icon, required this.label, required this.onTap, required this.enabled});
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: enabled ? 1 : 0.45,
      child: InkWell(
        onTap: enabled ? onTap : null,
        borderRadius: BorderRadius.circular(12),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 22),
          decoration: BoxDecoration(
            color: SokoColors.surface,
            borderRadius: BorderRadius.circular(12),
            boxShadow: const [BoxShadow(color: Color(0x14231F20), blurRadius: 6, offset: Offset(0, 2))],
          ),
          child: Column(children: [
            Icon(icon, color: SokoColors.orange, size: 32),
            const SizedBox(height: 8),
            Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
          ]),
        ),
      ),
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: const Color(0xFFFFF3E0), borderRadius: BorderRadius.circular(8)),
        child: Text(text, style: const TextStyle(color: SokoColors.warning)),
      );
}
