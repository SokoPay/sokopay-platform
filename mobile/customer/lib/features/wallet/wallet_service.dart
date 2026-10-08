import 'package:sokopay_shared/sokopay_shared.dart';

/// Wallet data access (DEMI feature), hitting:
///   GET  /wallet            → balance + activity
///   POST /wallet/fund       {amount, network}
///   GET  /wallet/send/lookup?account=  → {found, name}  (phone or wallet ID)
///   POST /wallet/send       {recipient, amount}       (phone or wallet ID)
class WalletService {
  WalletService(this._api);
  final ApiClient _api;

  Future<WalletState> load() async {
    final r = await _api.get('/wallet');
    return WalletState.fromJson(r.data);
  }

  Future<void> fund(String amount, String network) =>
      _api.post('/wallet/fund', data: {'amount': amount, 'network': network});

  /// Who owns this phone number / wallet ID? Returns the short name ("Ama M.") or null.
  Future<String?> lookup(String account) async {
    final r = await _api.get('/wallet/send/lookup', query: {'account': account});
    return r.data['found'] == true ? r.data['name'] as String? : null;
  }

  Future<Map<String, dynamic>> send(String recipient, String amount) async {
    final r = await _api.post('/wallet/send', data: {'recipient': recipient, 'amount': amount});
    return Map<String, dynamic>.from(r.data);
  }
}

class WalletState {
  WalletState({required this.balanceDisplay, required this.activity, this.walletNumber = ''});
  final String balanceDisplay;
  final String walletNumber; // display form, e.g. "7123 456 789"
  final List<WalletEntry> activity;
  factory WalletState.fromJson(Map<String, dynamic> j) => WalletState(
        balanceDisplay: j['balance_display'] ?? 'GH₵ 0.00',
        walletNumber: j['wallet_number_display'] ?? '',
        activity: (j['activity'] as List? ?? [])
            .map((e) => WalletEntry.fromJson(e))
            .toList(),
      );
}

class WalletEntry {
  WalletEntry({required this.direction, required this.amount, required this.narrative});
  final String direction; // in | out
  final String amount;
  final String narrative;
  factory WalletEntry.fromJson(Map<String, dynamic> j) => WalletEntry(
        direction: j['direction'] ?? 'in',
        amount: j['amount'] ?? '',
        narrative: j['narrative'] ?? '',
      );
}
