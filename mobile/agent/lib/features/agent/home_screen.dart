import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'agent_service.dart';

/// Agent home: shows float balance and the two core actions (cash-in, cash-out).
class AgentHomeScreen extends StatefulWidget {
  const AgentHomeScreen({super.key});
  @override
  State<AgentHomeScreen> createState() => _AgentHomeScreenState();
}

class _AgentHomeScreenState extends State<AgentHomeScreen> {
  late final AgentService _service;
  late Future<AgentProfile> _profile;
  Future<List<AgentTxn>>? _txns;

  @override
  void initState() {
    super.initState();
    _service = AgentService(context.read<ApiClient>());
    _refresh();
  }

  void _refresh() {
    setState(() {
      _profile = _service.me();
      _txns = _service.transactions();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('SokoPay Agent'),
        actions: [
          IconButton(
            tooltip: 'Security',
            onPressed: () => context.push('/security'),
            icon: const Icon(Icons.lock_outline),
          ),
          IconButton(
            tooltip: 'Notifications',
            onPressed: () async {
              await context.push('/inbox');
              _refresh(); // a float notification may have been about a top-up
            },
            icon: const Icon(Icons.notifications_none),
          ),
          IconButton(
            onPressed: () => context.read<AuthController>().signOut(),
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async => _refresh(),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            FutureBuilder<AgentProfile>(
              future: _profile,
              builder: (context, snap) {
                final float = snap.data?.floatDisplay ?? '—';
                return Container(
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(
                    color: SokoColors.ink,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Your float',
                          style: TextStyle(color: Colors.white70)),
                      const SizedBox(height: 6),
                      Text(float,
                          style: const TextStyle(
                              color: Colors.white,
                              fontSize: 30,
                              fontWeight: FontWeight.w700)),
                      if ((snap.data?.commissionMinor ?? 0) > 0) ...[
                        const SizedBox(height: 6),
                        Text('Commission earned: ${snap.data!.commissionDisplay} (paid into your float monthly)',
                            style: const TextStyle(color: SokoColors.orange)),
                      ],
                    ],
                  ),
                );
              },
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: _ActionCard(
                    icon: Icons.south_west,
                    label: 'Cash in',
                    color: SokoColors.success,
                    onTap: () async {
                      await context.push('/cash-in');
                      _refresh();
                    },
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _ActionCard(
                    icon: Icons.north_east,
                    label: 'Cash out',
                    color: SokoColors.orange,
                    onTap: () async {
                      await context.push('/cash-out');
                      _refresh();
                    },
                  ),
                ),
              ],
            ),
            const SizedBox(height: 24),
            Row(children: [
              const Expanded(child: Text('Recent', style: TextStyle(fontWeight: FontWeight.w600))),
              TextButton(
                onPressed: () => context.push('/history'),
                child: const Text('See all'),
              ),
            ]),
            const SizedBox(height: 8),
            FutureBuilder<List<AgentTxn>>(
              future: _txns,
              builder: (context, snap) {
                final txns = snap.data ?? [];
                if (txns.isEmpty) {
                  return const Padding(
                    padding: EdgeInsets.all(16),
                    child: Text('No transactions yet.',
                        style: TextStyle(color: Colors.black45)),
                  );
                }
                return Column(
                  children: txns
                      .map((t) => ListTile(
                            leading: Icon(t.kind == 'cash_in'
                                ? Icons.south_west
                                : Icons.north_east),
                            title: Text(t.kind.replaceAll('_', ' ')),
                            subtitle: Text(t.customerPhone),
                            trailing: Text('GH₵ ${(t.amountMinor / 100).toStringAsFixed(2)}'),
                          ))
                      .toList(),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _ActionCard extends StatelessWidget {
  const _ActionCard(
      {required this.icon, required this.label, required this.color, required this.onTap});
  final IconData icon;
  final String label;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 24),
        decoration: BoxDecoration(
          color: SokoColors.surface,
          borderRadius: BorderRadius.circular(12),
          boxShadow: const [
            BoxShadow(color: Color(0x14231F20), blurRadius: 6, offset: Offset(0, 2))
          ],
        ),
        child: Column(
          children: [
            Icon(icon, color: color, size: 32),
            const SizedBox(height: 8),
            Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
          ],
        ),
      ),
    );
  }
}
