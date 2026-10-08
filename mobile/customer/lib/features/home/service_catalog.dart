import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

/// The home screen's service tabs and tiles. Each tile either opens a working screen
/// ([route]) or — for services SokoPay will aggregate from licensed partners that
/// aren't connected yet — explains what's coming ([soon]). Nothing pretends to work.
class ServiceTile {
  const ServiceTile(this.label, this.icon, {this.route, this.tab, this.soon});
  final String label;
  final IconData icon;
  final String? route; // pushed
  final int? tab; // switch bottom-nav tab instead (e.g. Transfers)
  final ComingSoon? soon;
}

class ComingSoon {
  const ComingSoon(this.title, this.message);
  final String title;
  final String message;
}

const crossBorder = ComingSoon('Cross-border payments',
    'Send money to Nigeria, Kenya, Côte d\'Ivoire, Senegal and more. We are connecting licensed '
    'cross-border partners (such as Onafriq and Brij) — this will open once Bank of Ghana approval '
    'and the partner connection are in place.');

/// Tab → tiles. Transfers lives in the bottom bar too (tab index 1).
const serviceTabs = <String, List<ServiceTile>>{
  'For you': [
    ServiceTile('Transfers', Icons.send_outlined, tab: 1),
    ServiceTile('Bundles', Icons.wifi, route: '/data'),
    ServiceTile('Airtime', Icons.phone_in_talk_outlined, route: '/bills?category=airtime'),
    ServiceTile('Pay bills', Icons.receipt_long_outlined, route: '/bills'),
    ServiceTile('Save', Icons.savings_outlined, route: '/providers/savings'),
    ServiceTile('Invest', Icons.show_chart, route: '/providers/investment'),
    ServiceTile('Insure', Icons.umbrella_outlined, route: '/marketplace'),
    ServiceTile('Loans', Icons.volunteer_activism_outlined, route: '/marketplace'),
  ],
  'Pay': [
    ServiceTile('Transfers', Icons.send_outlined, tab: 1),
    ServiceTile('Electricity', Icons.lightbulb_outline, route: '/bills?category=electricity'),
    ServiceTile('Water', Icons.water_drop_outlined, route: '/bills?category=water'),
    ServiceTile('TV', Icons.tv, route: '/bills?category=tv'),
    ServiceTile('Internet', Icons.router_outlined, route: '/bills?category=internet'),
    ServiceTile('School fees', Icons.school_outlined, route: '/bills?category=education'),
    ServiceTile('Merchant', Icons.storefront_outlined, route: '/merchants'),
    ServiceTile('Fuel', Icons.local_gas_station_outlined, route: '/scan'), // pay at the pump by QR
    ServiceTile('General payments', Icons.description_outlined, route: '/bills'),
  ],
  'Buy': [
    ServiceTile('Airtime', Icons.phone_in_talk_outlined, route: '/bills?category=airtime'),
    ServiceTile('Bundles', Icons.wifi, route: '/data'),
  ],
  'Lifestyle': [
    ServiceTile('Food', Icons.restaurant_outlined, route: '/lifestyle/food'),
    ServiceTile('Tickets', Icons.confirmation_number_outlined, route: '/lifestyle/ticketing'),
  ],
  'Finance': [
    ServiceTile('Bank', Icons.account_balance_outlined, route: '/transfer?type=bank'),
    ServiceTile('Loans', Icons.volunteer_activism_outlined, route: '/marketplace'),
    ServiceTile('Insure', Icons.verified_user_outlined, route: '/marketplace'),
    ServiceTile('Save', Icons.savings_outlined, route: '/providers/savings'),
    ServiceTile('Invest', Icons.show_chart, route: '/providers/investment'),
    ServiceTile('Pension', Icons.elderly_outlined, route: '/providers/pension'),
  ],
};

void showComingSoon(BuildContext context, ComingSoon info) {
  showModalBottomSheet(
    context: context,
    builder: (_) => SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const Icon(Icons.handshake_outlined, size: 48, color: SokoColors.orange),
          const SizedBox(height: 12),
          Text(info.title, textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
          const SizedBox(height: 4),
          const Text('Coming soon', textAlign: TextAlign.center, style: TextStyle(color: SokoColors.orange)),
          const SizedBox(height: 12),
          Text(info.message, textAlign: TextAlign.center, style: const TextStyle(color: SokoColors.inkMuted)),
          const SizedBox(height: 20),
          ElevatedButton(onPressed: () => Navigator.pop(context), child: const Text('OK')),
        ]),
      ),
    ),
  );
}

/// A rounded square icon tile with a label underneath (home grids, Transfers hub).
class ServiceTileView extends StatelessWidget {
  const ServiceTileView({super.key, required this.tile, required this.onTab});
  final ServiceTile tile;
  final void Function(int tab) onTab;

  void _open(BuildContext context) {
    if (tile.soon != null) {
      showComingSoon(context, tile.soon!);
    } else if (tile.tab != null) {
      onTab(tile.tab!);
    } else if (tile.route != null) {
      context.push(tile.route!);
    }
  }

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => _open(context),
      borderRadius: BorderRadius.circular(14),
      child: Column(children: [
        Container(
          width: 64,
          height: 64,
          decoration: BoxDecoration(
            color: SokoColors.surface,
            borderRadius: BorderRadius.circular(14),
            boxShadow: const [BoxShadow(color: Color(0x14231F20), blurRadius: 6, offset: Offset(0, 2))],
          ),
          child: Stack(alignment: Alignment.center, children: [
            Icon(tile.icon, color: SokoColors.ink, size: 28),
            if (tile.soon != null)
              const Positioned(top: 6, right: 6, child: CircleAvatar(radius: 3, backgroundColor: SokoColors.orange)),
          ]),
        ),
        const SizedBox(height: 6),
        Text(tile.label, textAlign: TextAlign.center, maxLines: 2,
            style: const TextStyle(fontSize: 12.5, color: SokoColors.ink)),
      ]),
    );
  }
}
