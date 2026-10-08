import 'package:sokopay_shared/sokopay_shared.dart';

/// Transfers out of the SokoPay wallet to other institutions (DEMI):
///   POST /wallet/transfer/lookup   {destination_type, institution, account}
///   POST /wallet/transfer          {… , amount, narrative}
///   GET  /wallet/transfer          recent transfers
class TransferService {
  TransferService(this._api);
  final ApiClient _api;

  Future<RecipientCheck> lookup(TransferTarget t) async {
    final r = await _api.post('/wallet/transfer/lookup', data: t.toJson());
    return RecipientCheck.fromJson(r.data);
  }

  Future<TransferRecord> send(TransferTarget t, String amount, String narrative,
      String idempotencyKey) async {
    final r = await _api.post('/wallet/transfer',
        data: {...t.toJson(), 'amount': amount, 'narrative': narrative},
        headers: {'Idempotency-Key': idempotencyKey});
    return TransferRecord.fromJson(r.data);
  }

  Future<List<TransferRecord>> history() async {
    final r = await _api.get('/wallet/transfer');
    return (r.data as List).map((e) => TransferRecord.fromJson(e)).toList();
  }
}

class TransferTarget {
  TransferTarget(this.type, this.institution, this.account);
  final String type;        // momo | bank | wallet
  final String institution; // mtn / telecel / at | bank code | gmoney / zeepay
  final String account;
  Map<String, dynamic> toJson() =>
      {'destination_type': type, 'institution': institution, 'account': account};
}

class RecipientCheck {
  RecipientCheck({required this.found, required this.name, required this.supported,
      required this.message});
  final bool found;
  final String name;
  final bool supported;
  final String message;
  factory RecipientCheck.fromJson(Map<String, dynamic> j) => RecipientCheck(
        found: j['found'] == true,
        name: j['account_name'] ?? '',
        supported: j['supported'] != false,
        message: j['message'] ?? '',
      );
}

class TransferRecord {
  TransferRecord({required this.reference, required this.status, required this.amount,
      required this.accountName, required this.account, required this.institution,
      required this.type});
  final String reference;
  final String status; // pending | succeeded | failed
  final String amount;
  final String accountName;
  final String account;
  final String institution;
  final String type;
  factory TransferRecord.fromJson(Map<String, dynamic> j) => TransferRecord(
        reference: j['reference'],
        status: j['status'],
        amount: j['amount'],
        accountName: j['account_name'] ?? '',
        account: j['account'] ?? '',
        institution: j['institution'] ?? '',
        type: j['destination_type'] ?? '',
      );
}
