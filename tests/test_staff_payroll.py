from models import Expense, SalaryPayment, StaffUser, Settings
from tests.conftest import csrf_token
from tests.test_roles import _session_as, _staff


def test_owner_can_create_staff_profile_and_update_owner_contract_info(client, db_session):
    owner, password = _staff(db_session, "payroll-owner", "owner")
    _session_as(client, owner, password)
    token = csrf_token(client, "/admin/staff")

    response = client.post("/admin/staff", data={
        "csrf_token": token,
        "username": "store-cashier",
        "password": "cashier-pass",
        "role": "cashier",
        "full_name": "کارمند فروش",
        "employee_code": "EMP-7",
        "national_id": "0012345678",
        "phone": "09120000000",
        "job_title": "صندوقدار",
        "employment_type": "full_time",
        "salary_amount": "12000000",
        "salary_payment_day": "30",
        "hire_date": "1405/06/01",
        "contract_end_date": "1406/06/01",
        "work_schedule": "شنبه تا پنجشنبه",
        "bank_account": "123456789",
        "iban": "IR000000000000000000000000",
    }, follow_redirects=False)
    assert response.status_code == 303

    employee = db_session.query(StaffUser).filter(StaffUser.username == "store-cashier").one()
    assert employee.full_name == "کارمند فروش"
    assert employee.employee_code == "EMP-7"
    assert employee.salary_amount == 12_000_000
    assert employee.hire_date is not None

    token = csrf_token(client, "/admin/owner-profile")
    response = client.post("/admin/owner-profile", data={
        "csrf_token": token,
        "owner_full_name": "مالک فروشگاه",
        "owner_business_name": "رای کیدز",
        "owner_national_id": "0098765432",
        "owner_phone": "09121111111",
        "owner_email": "owner@example.test",
        "owner_business_registration": "REG-1",
        "owner_signatory_title": "کارفرما",
        "owner_address": "تهران",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert db_session.query(Settings).filter(Settings.key == "owner_full_name", Settings.value == "مالک فروشگاه").count() == 1


def test_salary_payment_creates_monthly_expense_and_printable_documents(client, db_session):
    owner, password = _staff(db_session, "salary-owner", "owner")
    employee, _ = _staff(db_session, "salary-cashier", "cashier")
    employee.full_name = "کارمند حقوقی"
    employee.employee_code = "EMP-9"
    employee.salary_amount = 10_000_000
    db_session.commit()
    _session_as(client, owner, password)

    token = csrf_token(client, "/admin/staff")
    response = client.post(f"/admin/staff/{employee.id}/salary", data={
        "csrf_token": token,
        "period_key": "1405-06",
        "deductions": "1000000",
        "payment_method": "cash",
        "note": "حقوق شهریور",
    }, follow_redirects=False)
    assert response.status_code == 303
    payment = db_session.query(SalaryPayment).filter(SalaryPayment.staff_user_id == employee.id).one()
    assert payment.net_amount == 9_000_000
    expense = db_session.query(Expense).filter(Expense.id == payment.expense_id).one()
    assert expense.amount == 9_000_000
    assert expense.expense_type == "monthly"
    assert expense.category == "حقوق کارکنان"

    duplicate = client.post(f"/admin/staff/{employee.id}/salary", data={
        "csrf_token": token,
        "period_key": "1405-06",
        "deductions": "0",
        "payment_method": "cash",
    }, follow_redirects=False)
    assert duplicate.status_code == 303
    assert db_session.query(SalaryPayment).filter(SalaryPayment.staff_user_id == employee.id).count() == 1

    receipt = client.get(f"/admin/payroll/{payment.id}/receipt")
    assert receipt.status_code == 200
    assert "رسید پرداخت حقوق" in receipt.text
    assert "کارمند حقوقی" in receipt.text

    contract = client.get(f"/admin/staff/{employee.id}/contract")
    assert contract.status_code == 200
    assert "قرارداد همکاری و استخدام" in contract.text
    assert "کارمند حقوقی" in contract.text
