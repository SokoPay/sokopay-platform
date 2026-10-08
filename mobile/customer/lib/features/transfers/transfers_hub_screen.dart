import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../activity/activity_service.dart';
import '../home/service_catalog.dart';

/// Transfers tab: where to send (SokoPay user, mobile money, bank, other wallets,
/// cross-border) and the people you recently paid / were paid by.
class TransfersHubScreen extends StatefulWidget {
  const TransfersHubScreen({super.key});
  @override
  State<TransfersHubScreen> createState() => _TransfersHubScreenState();
}

class _TransfersHubScreenState extends State<TransfersHubScreen> {
  static const _tiles = <ServiceTile>[
    ServiceTile('SokoPay user', Icons.person_outline, route: '/wallet/send'),
    ServiceTile('Mobile money', Icons.phone_android, route: '/transfer?type=momo'),
    ServiceTile('Bank account', Icons.account_balance_outlined, route: '/transfer?type=bank'),
    ServiceTile('Other wallets', Icons.account_balance_wallet_outlined, route: '/transfer?type=wallet'),
    ServiceTile('Cross border', Icons.public, route: '/cross-border'),
    ServiceTile('View all', Icons.more_horiz, route: '/activity?filter=transfers'),
  ];

  late Future<ActivityPage> _recent;

  @override
  void initState() {
    super.initState();
    _recent = ActivityService(context.read<ApiClient>()).page(filter: 'transfers');
  }

  Future<void> _refresh() async {
    final next = ActivityService(context.read<ApiClient>()).page(filter: 'transfers');
    setState(() => _recent = next);
    await next.catchError((_) => ActivityPage(results: const []));
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(padding: EdgeInsets.zero, children: [
        Stack(children: [
          Container(
            height: 150,
            decoration: const BoxDecoration(
              gradient: LinearGradient(begin: Alignment.topCenter, end: Alignment.bottomCenter,
                  colors: [SokoColors.ink, Color(0xFF3A3435)]),
              borderRadius: BorderRadius.vertical(bottom: Radius.elliptical(400, 40)),
            ),
          ),
          SafeArea(
            bottom: false,
            child: Column(children: [
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 14),
                child: Text('Transfer', style: TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w700)),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: GridView.count(
                  crossAxisCount: 3,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 12,
                  childAspectRatio: 1.05,
                  children: _tiles.map((t) => _HubTile(tile: t)).toList(),
                ),
              ),
            ]),
          ),
        ]),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 20, 8, 0),
          child: Row(children: [
            const Expanded(
              child: Text('RECENTLY PAID',
                  style: TextStyle(fontWeight: FontWeight.w800, color: SokoColors.ink, letterSpacing: 0.5)),
            ),
            TextButton(
              onPressed: () => context.push('/activity?filter=transfers'),
              child: const Text('See all'),
            ),
          ]),
        ),
        Container(height: 3, width: 120, margin: const EdgeInsets.only(left: 16), color: SokoColors.orange),
        FutureBuilder<ActivityPage>(
          future: _recent,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator()));
            }
            final items = (snap.data?.results ?? const <ActivityItem>[]).take(10).toList();
            if (snap.hasError || items.isEmpty) {
              return const Padding(
                padding: EdgeInsets.all(32),
                child: Text('No transfers yet.', textAlign: TextAlign.center, style: TextStyle(color: Colors.black45)),
              );
            }
            return Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 100),
              child: Column(children: items.map((i) => _RecentCard(item: i)).toList()),
            );
          },
        ),
      ]),
    );
  }
}

class _HubTile extends StatelessWidget {
  const _HubTile({required this.tile});
  final ServiceTile tile;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: SokoColors.surface,
      borderRadius: BorderRadius.circular(16),
      elevation: 2,
      shadowColor: const Color(0x33231F20),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: () => tile.soon != null ? showComingSoon(context, tile.soon!) : context.push(tile.route!),
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(tile.icon, color: SokoColors.ink, size: 30),
            const SizedBox(height: 8),
            Text(tile.label, textAlign: TextAlign.center, maxLines: 2, style: const TextStyle(fontSize: 13)),
          ]),
        ),
      ),
    );
  }
}

class _RecentCard extends StatelessWidget {
  const _RecentCard({required this.item});
  final ActivityItem item;

  @override
  Widget build(BuildContext context) {
    final name = item.title.replaceFirst(RegExp(r'^(To|From) '), '').toUpperCase();
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: SokoColors.surface,
        borderRadius: BorderRadius.circular(14),
        boxShadow: const [BoxShadow(color: Color(0x14231F20), blurRadius: 6, offset: Offset(0, 2))],
      ),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
            const SizedBox(height: 4),
            Text(DateFormat('dd MMM yyyy | HH:mm').format(item.createdAt),
                style: const TextStyle(color: SokoColors.inkMuted, fontSize: 12.5)),
            const SizedBox(height: 4),
            Text('Transaction type: ${item.isIn ? 'Received' : 'Transfer'}',
                style: const TextStyle(color: SokoColors.inkMuted, fontSize: 12.5)),
          ]),
        ),
        Text(item.amountDisplay.replaceAll(' ', ''),
            style: TextStyle(
              fontWeight: FontWeight.w700,
              color: item.isIn ? SokoColors.success : SokoColors.danger,
            )),
      ]),
    );
  }
}
