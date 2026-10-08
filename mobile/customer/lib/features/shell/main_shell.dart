import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

/// Bottom navigation: Home · Transfers · [Scan QR] · Offers · More. Each tab keeps its
/// own navigation stack (go_router StatefulShellRoute); Scan QR is the raised centre
/// button and opens the scanner over everything.
class MainShell extends StatelessWidget {
  const MainShell({super.key, required this.shell});
  final StatefulNavigationShell shell;

  void _go(int index) => shell.goBranch(index, initialLocation: index == shell.currentIndex);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: shell,
      extendBody: true,
      floatingActionButtonLocation: FloatingActionButtonLocation.centerDocked,
      floatingActionButton: SizedBox(
        width: 72,
        height: 72,
        child: FloatingActionButton(
          heroTag: 'scan',
          tooltip: 'Scan QR',
          backgroundColor: SokoColors.orange,
          shape: const CircleBorder(),
          elevation: 4,
          onPressed: () => context.push('/scan'),
          child: const Icon(Icons.qr_code_scanner, color: Colors.white, size: 34),
        ),
      ),
      bottomNavigationBar: BottomAppBar(
        color: SokoColors.surface,
        shape: const CircularNotchedRectangle(),
        notchMargin: 6,
        height: 72,
        padding: EdgeInsets.zero,
        child: Row(children: [
          _NavItem(icon: Icons.home_outlined, active: Icons.home, label: 'Home', selected: shell.currentIndex == 0, onTap: () => _go(0)),
          _NavItem(icon: Icons.send_outlined, active: Icons.send, label: 'Transfers', selected: shell.currentIndex == 1, onTap: () => _go(1)),
          const Expanded(child: Align(alignment: Alignment.bottomCenter,
              child: Padding(padding: EdgeInsets.only(bottom: 10),
                  child: Text('Scan QR', style: TextStyle(fontSize: 12, color: SokoColors.inkMuted))))),
          _NavItem(icon: Icons.card_giftcard_outlined, active: Icons.card_giftcard, label: 'Offers', selected: shell.currentIndex == 2, onTap: () => _go(2)),
          _NavItem(icon: Icons.more_horiz, active: Icons.more_horiz, label: 'More', selected: shell.currentIndex == 3, onTap: () => _go(3)),
        ]),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({required this.icon, required this.active, required this.label, required this.selected, required this.onTap});
  final IconData icon;
  final IconData active;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = selected ? SokoColors.orange : SokoColors.inkMuted;
    return Expanded(
      child: InkWell(
        onTap: onTap,
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(selected ? active : icon, color: color),
          const SizedBox(height: 2),
          Text(label, style: TextStyle(fontSize: 12, color: color, fontWeight: selected ? FontWeight.w700 : FontWeight.w400)),
        ]),
      ),
    );
  }
}
