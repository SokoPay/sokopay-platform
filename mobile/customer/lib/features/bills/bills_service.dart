import 'package:sokopay_shared/sokopay_shared.dart';

/// Data access for billers and bill payments, hitting:
///   GET  /billers[?category=]
///   GET  /billers/{code}/lookup?account=
///   POST /payments/bill           (Idempotency-Key header)
///   GET  /payments/{reference}
///
/// `source` is "momo" (approve a mobile-money prompt) or "wallet" (pay from the
/// SokoPay wallet — available once the DEMI licence is active).
class BillsService {
  BillsService(this._api);
  final ApiClient _api;

  Future<List<Biller>> billers({String? category}) async {
    final r = await _api.get('/billers',
        query: category != null ? {'category': category} : null);
    return (r.data as List).map((e) => Biller.fromJson(e)).toList();
  }

  Future<AccountLookupResult> lookup(String billerCode, String account) async {
    final r = await _api.get('/billers/$billerCode/lookup', query: {'account': account});
    return AccountLookupResult.fromJson(r.data);
  }

  Future<PaymentResult> payBill({
    required String billerCode,
    required String account,
    required String amount,
    required String source,
    String? network,
    String? payer,
    required String idempotencyKey,
    String? pin,
  }) async {
    final r = await _api.post('/payments/bill',
        data: {
          'biller_code': billerCode,
          'account': account,
          'amount': amount,
          'source': source,
          if (pin != null) 'pin': pin,
          if (network != null) 'network': network,
          if (payer != null) 'payer': payer,
        },
        headers: {'Idempotency-Key': idempotencyKey});
    return PaymentResult.fromJson(r.data);
  }

  Future<PaymentResult> buyAirtime({
    required String billerCode,
    required String phone,
    required String amount,
    required String source,
    String? network,
    String? payer,
    required String idempotencyKey,
    String? pin,
  }) async {
    final r = await _api.post('/payments/airtime',
        data: {
          'biller_code': billerCode,
          'phone': phone,
          'amount': amount,
          'source': source,
          if (pin != null) 'pin': pin,
          if (network != null) 'network': network,
          if (payer != null) 'payer': payer,
        },
        headers: {'Idempotency-Key': idempotencyKey});
    return PaymentResult.fromJson(r.data);
  }

  Future<PaymentResult> status(String reference) async {
    final r = await _api.get('/payments/$reference');
    return PaymentResult.fromJson(r.data);
  }
}

class Biller {
  Biller({required this.code, required this.name, required this.category,
      this.network, this.supportsLookup = false});
  final String code;
  final String name;
  final String category;
  final String? network;
  final bool supportsLookup;
  factory Biller.fromJson(Map<String, dynamic> j) => Biller(
        code: j['code'],
        name: j['name'],
        category: j['category'],
        network: (j['network'] as String?)?.isEmpty == true ? null : j['network'],
        supportsLookup: j['supports_lookup'] == true,
      );
}

class AccountLookupResult {
  AccountLookupResult({required this.found, required this.accountName,
      required this.supported, required this.message});
  final bool found;
  final String accountName;
  final bool supported;
  final String message;
  factory AccountLookupResult.fromJson(Map<String, dynamic> j) => AccountLookupResult(
        found: j['found'] == true,
        accountName: j['account_name'] ?? '',
        supported: j['supported'] != false,
        message: j['message'] ?? '',
      );
}

class PaymentResult {
  PaymentResult({required this.reference, required this.status, required this.total,
      this.deliveryToken = '', this.failureCode = ''});
  final String reference;
  final String status; // pending | succeeded | failed | refunded
  final String total;
  final String deliveryToken; // e.g. ECG prepaid token
  final String failureCode;
  factory PaymentResult.fromJson(Map<String, dynamic> j) => PaymentResult(
        reference: j['reference'],
        status: j['status'],
        total: j['total'],
        deliveryToken: j['delivery_token'] ?? '',
        failureCode: j['failure_code'] ?? '',
      );
}
