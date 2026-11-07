"""Offline tests: no secrets or shared database connection."""
import pytest
from scripts.shared_llama_benchmark import (
    role_violations,TAGS,initialize_readonly_guards,TRANSACTION_GUARDS_SQL,VERIFY_GUARDS_SQL,
)


@pytest.mark.parametrize("privilege",["bypass_rls","database_owner","create_roles","create_databases",
    "replication","assumable_privileged_role","session_role_changed","persistent_relation_write_or_owner",
    "schema_create_or_owner","executable_security_definer"])
def test_readonly_transaction_does_not_excuse_privileged_account(privilege):
    flags={"transaction_read_only":"on",privilege:True}
    assert role_violations(flags)==[privilege]


def test_temporary_object_capability_is_not_used_or_mistaken_for_persistent_access():
    assert role_violations({"transaction_read_only":"on","temporary_objects_possible":True,
        "persistent_relation_write_or_owner":False})==[]


def test_requested_model_variants_are_exact_and_no_70b_or_fictional_2b():
    assert TAGS==("llama3.2:1b","llama3.2:3b","llama3:8b","llama3.1:8b")


class GuardConnection:
    def __init__(self, settings):
        self.settings=settings
        self.executed=[]

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.executed.append(sql)

    def fetchone(self):
        return self.settings if self.executed[-1]==VERIFY_GUARDS_SQL else None


@pytest.mark.parametrize("index,bad_value",[(0,"off"),(1,"2min"),(2,"0"),(3,"0"),(4,"public")])
def test_missing_transaction_guard_fails_before_any_application_read(index,bad_value):
    settings=["on","5s","1s","10s","pg_catalog"]
    settings[index]=bad_value
    connection=GuardConnection(tuple(settings))
    with pytest.raises(ValueError,match="could not be verified"):
        initialize_readonly_guards(connection)
    assert connection.executed==[TRANSACTION_GUARDS_SQL,VERIFY_GUARDS_SQL]


def test_transaction_guards_are_verified_after_setting_pooler_safe_local_limits():
    connection=GuardConnection(("on","5s","1s","10s","pg_catalog"))
    initialize_readonly_guards(connection)
    assert connection.executed==[TRANSACTION_GUARDS_SQL,VERIFY_GUARDS_SQL]
