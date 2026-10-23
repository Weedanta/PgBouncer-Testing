from playground.config import load_experiment
from playground.hostprobe import Core, plan_resources, read_topology
from playground.lock import combined, ini_value
from playground.render import (
    pool_size,
    render_pgbouncer_ini,
    render_postgresql_conf,
    rule_pool_size,
)

HOST = {"POSTGRES_PHYSICAL_CORES": "3", "MEM_POSTGRES_MB": "8192", "LAYOUT": "dedicated"}


def test_pool_size_rule_tabel_3_2():
    cfg = load_experiment()
    assert rule_pool_size(cfg, HOST) == 2 * 3 + 0
    assert pool_size(cfg, HOST, {"default_pool_size": {"value": 9, "reason": "x"}}) == 9


def test_pgbouncer_ini_values():
    ini = render_pgbouncer_ini(load_experiment(), HOST, {})
    expect = {
        "pool_mode": "transaction",
        "default_pool_size": "6",
        "max_db_connections": "6",
        "reserve_pool_size": "0",
        "max_client_conn": "1100",
        "query_wait_timeout": "120",
        "max_prepared_statements": "200",
        "auth_type": "scram-sha-256",
        "listen_port": "6432",
    }
    for key, value in expect.items():
        assert ini_value(ini, key) == value, key
    assert "bench = host=postgres port=5432 dbname=bench" in ini
    assert "bench_smoke = host=postgres" in ini


def test_postgresql_conf_control_variables():
    conf = render_postgresql_conf(load_experiment(), HOST)
    assert "max_connections = 1100" in conf
    assert "shared_buffers = 2048MB" in conf
    assert "maintenance_work_mem = 1024MB" in conf
    assert "fsync = on" in conf and "synchronous_commit = on" in conf


LSCPU = "CPU CORE SOCKET ONLINE\n" + "\n".join(f"{cpu} {cpu % 8} 0 yes" for cpu in range(16))


def test_topology_keeps_siblings_together():
    cores = read_topology(LSCPU)
    assert len(cores) == 8 and cores[0].cpus == [0, 8]


def test_dedicated_layout_split():
    cfg = load_experiment()
    plan = plan_resources(read_topology(LSCPU), 15600, cfg["resources"])
    assert plan["CPUSET_HOST"] == "0,8"
    assert plan["CPUSET_MONITOR"] == "1,9"
    assert plan["CPUSET_PGBOUNCER"] == "2,10"
    assert plan["CPUSET_CLIENT"] == "3,11,4,12"  # ceil(0.25 * 5) = 2 cores
    assert plan["CPUSET_POSTGRES"] == "5,13,6,14,7,15"
    assert plan["POSTGRES_PHYSICAL_CORES"] == 3
    assert plan["MEM_POSTGRES_MB"] == 15600 - 2048 - 1536 - 3072 - 256


def test_small_server_layout_shares_one_core():
    cfg = load_experiment()
    res = dict(cfg["resources"], layout="small_server")
    plan = plan_resources([Core(0, i, [i, i + 8]) for i in range(8)], 15600, res)
    assert plan["CPUSET_POSTGRES"] == plan["CPUSET_PGBOUNCER"] == "2,10"
    assert plan["MEM_POSTGRES_MB"] == 4096 - 128


def test_lock_hash_is_order_independent():
    assert combined({"a": "1", "b": "2"}) == combined({"b": "2", "a": "1"})
    assert combined({"a": "1"}) != combined({"a": "2"})
