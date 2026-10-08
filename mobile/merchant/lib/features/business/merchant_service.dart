import 'package:sokopay_shared/sokopay_shared.dart';

/// Merchant app data access — /merchant-app/* on the backend (apps/merchants/app_views.py).
/// Amounts travel as text cedis ("25.00") going up and integer pesewas coming back;
/// the app never does money arithmetic in floating point.
class MerchantService {
  MerchantService(this._api);
  final ApiClient _api;

  Future<BusinessProfile> me() async =>
      BusinessProfile.fromJson(Map<String, dynamic>.from((await _api.get('/merchant-app/me')).data));

  Future<StaticQr> staticQr() async =>
      StaticQr.fromJson(Map<String, dynamic>.from((await _api.get('/merchant-app/qr')).data));

  Future<PaymentRequestInfo> createRequest({String? amount, String? description}) async {
    final r = await _api.post('/merchant-app/payment-requests', data: {
      'amount': amount ?? '',
      'description': description ?? '',
    });
    return PaymentRequestInfo.fromJson(Map<String, dynamic>.from(r.data));
  }

  Future<PaymentRequestInfo> request(String token) async => PaymentRequestInfo.fromJson(
      Map<String, dynamic>.from((await _api.get('/merchant-app/payment-requests/$token')).data));

  Future<List<PaymentRequestInfo>> recentRequests() async {
    final r = await _api.get('/merchant-app/payment-requests');
    return (r.data as List).map((e) => PaymentRequestInfo.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<void> cancelRequest(String token) =>
      _api.post('/merchant-app/payment-requests/$token/cancel');

  /// One page of payment history, newest first. Pass the previous page's
  /// [PaymentsPage.nextCursor] to load more. [status]: paid|pending|failed|refunded;
  /// [period]: today|7d|30d (null = all time).
  Future<PaymentsPage> payments({String? status, String? period, String? cursor}) async {
    final r = await _api.get('/merchant-app/payments', query: {
      if (status != null) 'status': status,
      if (period != null) 'period': period,
      if (cursor != null) 'cursor': cursor,
    });
    return PaymentsPage.fromJson(Map<String, dynamic>.from(r.data));
  }

  Future<PaymentInfo> payment(String reference) async => PaymentInfo.fromJson(
      Map<String, dynamic>.from((await _api.get('/merchant-app/payments/$reference')).data));

  /// Refund all ([amount] null/empty) or part of a payment, back to the customer the
  /// way they paid. Owner/Finance only; the PIN is re-checked on the server.
  Future<void> refund(String reference, {String? amount, required String reason, required String pin}) =>
      _api.post('/merchant-app/payments/$reference/refund',
          data: {'amount': amount ?? '', 'reason': reason, 'pin': pin});

  /// Customer disputes. [closed] false = still active (waiting for you or SokoPay).
  Future<DisputesPage> disputes({bool closed = false}) async => DisputesPage.fromJson(Map<String, dynamic>.from(
      (await _api.get('/merchant-app/disputes', query: {if (closed) 'status': 'closed'})).data));

  /// Answer a dispute: [accept] refunds the customer (needs [pin]); otherwise
  /// [response] explains your side for SokoPay to review.
  Future<void> respondToDispute(String id, {required bool accept, String response = '', String? pin}) =>
      _api.post('/merchant-app/disputes/$id/respond',
          data: {'accept': accept, 'response': response, if (pin != null) 'pin': pin});

  Future<SettlementOverview> settlements() async => SettlementOverview.fromJson(
      Map<String, dynamic>.from((await _api.get('/merchant-app/settlements')).data));

  /// Returns the created settlement. [amount] null/empty = the whole available balance.
  Future<SettlementInfo> requestSettlement({String? amount, String? accountId, required String pin}) async {
    final r = await _api.post('/merchant-app/settlements', data: {
      'amount': amount ?? '',
      if (accountId != null) 'account_id': accountId,
      'pin': pin,
    });
    return SettlementInfo.fromJson(Map<String, dynamic>.from(r.data));
  }

  Future<void> addAccount({
    required String kind,
    required String provider,
    required String accountNo,
    required String accountName,
    required String pin,
  }) =>
      _api.post('/merchant-app/settlement-accounts', data: {
        'kind': kind,
        'provider': provider,
        'account_no': accountNo,
        'account_name': accountName,
        'pin': pin,
      });
}

class BusinessProfile {
  BusinessProfile({
    required this.name,
    required this.isLive,
    required this.role,
    required this.canViewMoney,
    required this.canMoveMoney,
    this.availableDisplay,
  });
  final String name;
  final bool isLive;
  final String role;
  final bool canViewMoney;
  final bool canMoveMoney;
  final String? availableDisplay;

  factory BusinessProfile.fromJson(Map<String, dynamic> j) => BusinessProfile(
        name: j['merchant_name'] ?? '',
        isLive: j['is_live'] == true,
        role: j['role'] ?? '',
        canViewMoney: j['can_view_money'] == true,
        canMoveMoney: j['can_move_money'] == true,
        availableDisplay: j['available_display'],
      );
}

class StaticQr {
  StaticQr({required this.merchantName, required this.shortCode, required this.payload});
  final String merchantName;
  final String shortCode;
  final String payload;
  factory StaticQr.fromJson(Map<String, dynamic> j) =>
      StaticQr(merchantName: j['merchant_name'] ?? '', shortCode: j['short_code'] ?? '', payload: j['payload'] ?? '');
}

class PaymentRequestInfo {
  PaymentRequestInfo({
    required this.token,
    required this.payload,
    required this.status,
    required this.expiresAt,
    required this.createdAt,
    this.amountDisplay,
    this.description = '',
  });
  final String token;
  final String payload;
  final String status; // open | paid | expired | cancelled
  final DateTime expiresAt;
  final DateTime createdAt;
  final String? amountDisplay; // null = customer enters the amount
  final String description;

  bool get isOpen => status == 'open';
  bool get isPaid => status == 'paid';

  factory PaymentRequestInfo.fromJson(Map<String, dynamic> j) => PaymentRequestInfo(
        token: j['token'],
        payload: j['payload'] ?? '',
        status: j['status'] ?? 'open',
        expiresAt: DateTime.parse(j['expires_at']).toLocal(),
        createdAt: DateTime.parse(j['created_at']).toLocal(),
        amountDisplay: j['amount_display'],
        description: j['description'] ?? '',
      );
}

class PaymentInfo {
  PaymentInfo({
    required this.reference,
    required this.status,
    required this.statusDisplay,
    required this.amountDisplay,
    required this.method,
    required this.payer,
    required this.createdAt,
    this.completedAt,
    this.note = '',
    this.isTest = false,
    this.feeDisplay,
    this.netDisplay,
    this.failure,
    this.refunds = const [],
    this.refundableDisplay,
    this.canRefund = false,
    this.dispute,
  });
  final List<RefundInfo> refunds; // money-role users only
  final String? refundableDisplay;
  final bool canRefund;
  final DisputeInfo? dispute; // an active dispute on this payment
  final String reference;
  final String status; // succeeded | pending | created | failed | refunded
  final String statusDisplay;
  final String amountDisplay;
  final String method; // "MTN MoMo", "SokoPay wallet", …
  final String payer; // masked by the server, e.g. +23324•••8519
  final DateTime createdAt;
  final DateTime? completedAt;
  final String note; // the QR request's description, if any
  final bool isTest;
  final String? feeDisplay; // only for roles allowed to see money
  final String? netDisplay;
  final String? failure;

  factory PaymentInfo.fromJson(Map<String, dynamic> j) => PaymentInfo(
        reference: j['reference'],
        status: j['status'] ?? '',
        statusDisplay: j['status_display'] ?? '',
        amountDisplay: j['amount_display'] ?? '',
        method: j['method'] ?? '',
        payer: j['payer'] ?? '',
        createdAt: DateTime.parse(j['created_at']).toLocal(),
        completedAt: j['completed_at'] == null ? null : DateTime.parse(j['completed_at']).toLocal(),
        note: j['note'] ?? '',
        isTest: j['is_test'] == true,
        feeDisplay: j['fee_display'],
        netDisplay: j['net_display'],
        failure: j['failure'],
        refunds: ((j['refunds'] as List?) ?? const [])
            .map((e) => RefundInfo.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
        refundableDisplay: j['refundable_display'],
        canRefund: j['can_refund'] == true,
        dispute: j['dispute'] == null ? null : DisputeInfo.fromJson(Map<String, dynamic>.from(j['dispute'])),
      );
}

class RefundInfo {
  RefundInfo({required this.amountDisplay, required this.statusDisplay, required this.reason,
      required this.destination, required this.createdAt});
  final String amountDisplay;
  final String statusDisplay;
  final String reason;
  final String destination;
  final DateTime createdAt;
  factory RefundInfo.fromJson(Map<String, dynamic> j) => RefundInfo(
        amountDisplay: j['amount_display'] ?? '',
        statusDisplay: j['status_display'] ?? '',
        reason: j['reason'] ?? '',
        destination: j['destination'] ?? '',
        createdAt: DateTime.parse(j['created_at']).toLocal(),
      );
}

class DisputeInfo {
  DisputeInfo({required this.id, required this.paymentReference, required this.status,
      required this.statusDisplay, required this.reasonDisplay, required this.description,
      required this.amountDisplay, required this.respondBy, this.customer = '', this.merchantResponse = '',
      this.decisionNote = ''});
  final String id;
  final String paymentReference;
  final String status; // open | responded | resolved_customer | resolved_merchant | withdrawn
  final String statusDisplay;
  final String reasonDisplay;
  final String description;
  final String amountDisplay;
  final DateTime respondBy;
  final String customer; // masked
  final String merchantResponse;
  final String decisionNote;
  bool get waitingForYou => status == 'open';
  factory DisputeInfo.fromJson(Map<String, dynamic> j) => DisputeInfo(
        id: j['id'],
        paymentReference: j['payment_reference'] ?? '',
        status: j['status'] ?? '',
        statusDisplay: j['status_display'] ?? '',
        reasonDisplay: j['reason_display'] ?? '',
        description: j['description'] ?? '',
        amountDisplay: j['amount_display'] ?? '',
        respondBy: DateTime.parse(j['respond_by']).toLocal(),
        customer: j['customer'] ?? '',
        merchantResponse: j['merchant_response'] ?? '',
        decisionNote: j['decision_note'] ?? '',
      );
}

class DisputesPage {
  DisputesPage({required this.results, required this.heldDisplay, required this.canRespond});
  final List<DisputeInfo> results;
  final String heldDisplay;
  final bool canRespond;
  factory DisputesPage.fromJson(Map<String, dynamic> j) => DisputesPage(
        results: ((j['results'] as List?) ?? const [])
            .map((e) => DisputeInfo.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
        heldDisplay: j['held_display'] ?? '',
        canRespond: j['can_respond'] == true,
      );
}

class PaymentsSummary {
  PaymentsSummary({required this.paidCount, required this.gross, required this.fees, required this.net});
  final int paidCount;
  final String gross;
  final String fees;
  final String net;
  factory PaymentsSummary.fromJson(Map<String, dynamic> j) => PaymentsSummary(
        paidCount: j['paid_count'] ?? 0,
        gross: j['gross_display'] ?? '',
        fees: j['fees_display'] ?? '',
        net: j['net_display'] ?? '',
      );
}

class PaymentsPage {
  PaymentsPage({required this.results, this.nextCursor, this.summary});
  final List<PaymentInfo> results;
  final String? nextCursor;
  final PaymentsSummary? summary; // first page, money roles only

  factory PaymentsPage.fromJson(Map<String, dynamic> j) => PaymentsPage(
        results: ((j['results'] ?? []) as List)
            .map((e) => PaymentInfo.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
        nextCursor: j['next_cursor'],
        summary: j['summary'] == null ? null : PaymentsSummary.fromJson(Map<String, dynamic>.from(j['summary'])),
      );
}

class SettlementAccountInfo {
  SettlementAccountInfo({
    required this.id,
    required this.kind,
    required this.provider,
    required this.accountNo,
    required this.accountName,
    required this.verified,
    required this.isDefault,
  });
  final String id;
  final String kind; // momo | bank
  final String provider;
  final String accountNo;
  final String accountName;
  final bool verified;
  final bool isDefault;

  String get label => '${kind == 'bank' ? 'Bank' : 'MoMo'} · ${provider.toUpperCase()} · $accountNo';

  factory SettlementAccountInfo.fromJson(Map<String, dynamic> j) => SettlementAccountInfo(
        id: j['id'],
        kind: j['kind'],
        provider: j['provider'] ?? '',
        accountNo: j['account_no'] ?? '',
        accountName: j['account_name'] ?? '',
        verified: j['verified'] == true,
        isDefault: j['is_default'] == true,
      );
}

class SettlementInfo {
  SettlementInfo({
    required this.id,
    required this.amountDisplay,
    required this.status,
    required this.statusDisplay,
    required this.destination,
    required this.createdAt,
    this.needsApproval = false,
  });
  final String id;
  final String amountDisplay;
  final String status; // awaiting_approval | processing | paid | failed | rejected
  final String statusDisplay;
  final String destination;
  final DateTime createdAt;
  final bool needsApproval;

  factory SettlementInfo.fromJson(Map<String, dynamic> j) => SettlementInfo(
        id: j['id'],
        amountDisplay: j['amount_display'] ?? '',
        status: j['status'] ?? '',
        statusDisplay: j['status_display'] ?? '',
        destination: j['destination'] ?? '',
        createdAt: DateTime.parse(j['created_at']).toLocal(),
        needsApproval: j['needs_approval'] == true,
      );
}

class SettlementOverview {
  SettlementOverview({
    required this.availableMinor,
    required this.availableDisplay,
    required this.canMoveMoney,
    required this.bankPayoutsAvailable,
    required this.accounts,
    required this.settlements,
  });
  final int availableMinor;
  final String availableDisplay;
  final bool canMoveMoney;
  final bool bankPayoutsAvailable;
  final List<SettlementAccountInfo> accounts;
  final List<SettlementInfo> settlements;

  factory SettlementOverview.fromJson(Map<String, dynamic> j) => SettlementOverview(
        availableMinor: j['available_minor'] ?? 0,
        availableDisplay: j['available_display'] ?? 'GH₵ 0.00',
        canMoveMoney: j['can_move_money'] == true,
        bankPayoutsAvailable: j['bank_payouts_available'] == true,
        accounts: ((j['accounts'] ?? []) as List)
            .map((e) => SettlementAccountInfo.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
        settlements: ((j['settlements'] ?? []) as List)
            .map((e) => SettlementInfo.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
      );
}
