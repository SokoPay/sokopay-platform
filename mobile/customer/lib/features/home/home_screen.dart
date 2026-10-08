import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../wallet/wallet_service.dart';
import 'service_catalog.dart';

/// Home: brand header (profile · logo · notifications), the wallet card (wallet ID,
/// balance with hide/show, Deposit · Cash out · Statements), and the service tabs
/// For you · Pay · Buy · Lifestyle · Finance.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.onTab});

  /// Switch the bottom-nav tab (0 Home, 1 Transfers, 2 Offers, 3 More).
  final void Function(int tab) onTab;
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late final ApiClient _api;
  WalletState? _wallet;
  bool _walletLicensed = true;
  int _unread = 0;
  bool _hideBalance = false;
  String _tab = 'For you';

  @override
  void initState() {
    super.initState();
    _api = context.read<ApiClient>();
    LocalPrefs.getBool('hide_balance').then((v) {
      if (mounted) setState(() => _hideBalance = v);
    });
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final w = await WalletService(_api).load();
      if (mounted) setState(() => _wallet = w);
    } on DioException catch (e) {
      // Before the e-money licence the wallet endpoint answers "not_licensed".
      final notLicensed = e.response?.statusCode == 403;
      if (mounted) setState(() => _walletLicensed = !notLicensed);
    } catch (_) {}
    try {
      final r = await _api.get('/notifications');
      final unread = (r.data as List).where((n) => n['read'] != true).length;
      if (mounted) setState(() => _unread = unread);
    } catch (_) {}
  }

  void _toggleHide() {
    setState(() => _hideBalance = !_hideBalance);
    LocalPrefs.setBool('hide_balance', _hideBalance);
  }

  @override
  Widget build(BuildContext context) {
    final tiles = serviceTabs[_tab]!;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(padding: EdgeInsets.zero, children: [
        Stack(children: [
          Container(
            height: 210,
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [SokoColors.ink, Color(0xFF3A3435)],
              ),
              borderRadius: BorderRadius.vertical(bottom: Radius.elliptical(400, 40)),
            ),
          ),
          SafeArea(
            bottom: false,
            child: Column(children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                child: Row(children: [
                  IconButton(
                    tooltip: 'Profile',
                    onPressed: () => widget.onTab(3),
                    icon: const Icon(Icons.account_circle_outlined, color: SokoColors.orange, size: 32),
                  ),
                  const Expanded(child: Center(child: SokoLogo(height: 26, onDark: true))),
                  IconButton(
                    tooltip: 'Notifications',
                    onPressed: () async {
                      await context.push('/inbox');
                      _refresh();
                    },
                    icon: Badge(
                      isLabelVisible: _unread > 0,
                      label: Text(_unread > 99 ? '99+' : '$_unread'),
                      child: const Icon(Icons.notifications_none, color: SokoColors.orange, size: 30),
                    ),
                  ),
                ]),
              ),
              const SizedBox(height: 8),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: _WalletCard(
                  wallet: _wallet,
                  licensed: _walletLicensed,
                  hidden: _hideBalance,
                  onToggleHide: _toggleHide,
                  onAction: (route) async {
                    await context.push(route);
                    _refresh();
                  },
                ),
              ),
            ]),
          ),
        ]),
        const SizedBox(height: 16),
        SizedBox(
          height: 44,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            children: serviceTabs.keys
                .map((t) => Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      child: ChoiceChip(
                        label: Text(t.toUpperCase(),
                            style: TextStyle(
                              fontWeight: FontWeight.w700,
                              letterSpacing: 0.5,
                              color: t == _tab ? Colors.white : SokoColors.inkMuted,
                            )),
                        selected: t == _tab,
                        selectedColor: SokoColors.orange,
                        backgroundColor: SokoColors.bg,
                        side: BorderSide.none,
                        showCheckmark: false,
                        shape: const StadiumBorder(),
                        onSelected: (_) => setState(() => _tab = t),
                      ),
                    ))
                .toList(),
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 100),
          child: GridView.count(
            crossAxisCount: 4,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 16,
            crossAxisSpacing: 8,
            childAspectRatio: 0.72,
            children: tiles.map((t) => ServiceTileView(tile: t, onTab: widget.onTab)).toList(),
          ),
        ),
      ]),
    );
  }
}

class _WalletCard extends StatelessWidget {
  const _WalletCard({
    required this.wallet,
    required this.licensed,
    required this.hidden,
    required this.onToggleHide,
    required this.onAction,
  });
  final WalletState? wallet;
  final bool licensed;
  final bool hidden;
  final VoidCallback onToggleHide;
  final void Function(String route) onAction;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: SokoColors.surface,
        borderRadius: BorderRadius.circular(16),
        boxShadow: const [BoxShadow(color: Color(0x22231F20), blurRadius: 12, offset: Offset(0, 4))],
      ),
      child: Column(children: [
        Stack(children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: const BoxDecoration(
              color: SokoColors.orange,
              borderRadius: BorderRadius.only(topLeft: Radius.circular(16), bottomRight: Radius.circular(10)),
            ),
            child: const Text('Wallet', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700)),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 28, 8, 16),
            child: !licensed
                ? const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: Text(
                      'Your SokoPay wallet opens once our e-money licence is granted. '
                      'Meanwhile, pay bills, airtime and shops with mobile money.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: SokoColors.inkMuted),
                    ),
                  )
                : Row(children: [
                    const SizedBox(width: 40),
                    Expanded(
                      child: Column(children: [
                        Text(wallet?.walletNumber ?? '—',
                            style: const TextStyle(color: SokoColors.ink, letterSpacing: 1.2, fontSize: 15)),
                        const SizedBox(height: 4),
                        Text(
                          hidden ? 'GH₵ ••••' : (wallet?.balanceDisplay ?? '…'),
                          style: const TextStyle(color: SokoColors.ink, fontSize: 30, fontWeight: FontWeight.w800),
                        ),
                      ]),
                    ),
                    IconButton(
                      tooltip: hidden ? 'Show balance' : 'Hide balance',
                      onPressed: onToggleHide,
                      icon: Icon(hidden ? Icons.visibility_outlined : Icons.visibility_off_outlined,
                          color: SokoColors.ink),
                    ),
                  ]),
          ),
        ]),
        const Divider(height: 1),
        IntrinsicHeight(
          child: Row(children: [
            if (licensed) ...[
              _CardAction(icon: Icons.add_card_outlined, label: 'Deposit', onTap: () => onAction('/wallet/fund')),
              const VerticalDivider(width: 1, indent: 12, endIndent: 12, color: SokoColors.orange),
              _CardAction(icon: Icons.local_atm_outlined, label: 'Cash out', onTap: () => onAction('/cashout')),
              const VerticalDivider(width: 1, indent: 12, endIndent: 12, color: SokoColors.orange),
            ],
            _CardAction(icon: Icons.swap_horiz, label: 'Statements', onTap: () => onAction('/activity')),
          ]),
        ),
      ]),
    );
  }
}

class _CardAction extends StatelessWidget {
  const _CardAction({required this.icon, required this.label, required this.onTap});
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 14),
          child: Column(children: [
            Icon(icon, color: SokoColors.ink),
            const SizedBox(height: 4),
            Text(label, style: const TextStyle(fontWeight: FontWeight.w700, color: SokoColors.ink)),
          ]),
        ),
      ),
    );
  }
}
