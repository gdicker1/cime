#!/usr/bin/env python3

"""
Script to create, build and run CIME tests. This script can:

1) Run a single test, or more than one test
   ./create_test TESTNAME
   ./create_test TESTNAME1 TESTNAME2 ...
2) Run a test suite from a text file with one test per line
   ./create_test -f TESTFILE
3) Run an E3SM test suite:
  Below, a suite name, SUITE, is defined in $CIMEROOT/scripts/lib/get_tests.py
  - Run a single suite
   ./create_test SUITE
  - Run two suites
   ./create_test SUITE1 SUITE2
  - Run all tests in a suite except for one
   ./create_test SUITE ^TESTNAME
  - Run all tests in a suite except for tests that are in another suite
   ./create_test SUITE1 ^SUITE2
  - Run all tests in a suite with baseline comparisons against master baselines
   ./create_test SUITE1 -c -b master
4) Run a CESM test suite(s):
   ./create_test --xml-category XML_CATEGORY [--xml-machine XML_MACHINE] [--xml-compiler XML_COMPILER] [ --xml-testlist XML_TESTLIST]

If this tool is missing any feature that you need, please add an issue on
https://github.com/ESMCI/cime
"""
from CIME.Tools.standard_script_setup import *
from CIME import get_tests
from CIME.test_scheduler import TestScheduler, RUN_PHASE
from CIME import utils
from CIME.utils import (
    expect,
    convert_to_seconds,
    compute_total_time,
    convert_to_babylonian_time,
    run_cmd_no_fail,
    get_cime_config,
)
from CIME.config import Config
from CIME.XML.machines import Machines
from CIME.case import Case
from CIME.test_utils import get_tests_from_xml
from argparse import RawTextHelpFormatter

import argparse, math, glob

logger = logging.getLogger(__name__)

###############################################################################
def parse_command_line(args, description):
    ###############################################################################

    parser = argparse.ArgumentParser(
        description=description, formatter_class=RawTextHelpFormatter
    )

    model_config = Config.instance()

    CIME.utils.setup_standard_logging_options(parser)

    config = get_cime_config()

    parser.add_argument(
        "--no-run", action="store_true", help="Do not run generated tests"
    )

    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Do not build generated tests, implies --no-run",
    )

    parser.add_argument(
        "--no-setup",
        action="store_true",
        help="Do not setup generated tests, implies --no-build and --no-run",
    )

    parser.add_argument(
        "-u",
        "--use-existing",
        action="store_true",
        help="Use pre-existing case directories they will pick up at the "
        "\nlatest PEND state or re-run the first failed state. Requires test-id",
    )

    default = get_default_setting(config, "SAVE_TIMING", False, check_main=False)

    parser.add_argument(
        "--save-timing",
        action="store_true",
        default=default,
        help="Enable archiving of performance data.",
    )

    parser.add_argument(
        "--no-batch",
        action="store_true",
        help="Do not submit jobs to batch system, run locally."
        "\nIf false, this will default to machine setting.",
    )

    parser.add_argument(
        "--single-exe",
        action="store_true",
        default=False,
        help="Use a single build for all cases. This can "
        "\ndrastically improve test throughput but is currently use-at-your-own risk."
        "\nIt's up to the user to ensure that all cases are build-compatible."
        "\nE3SM tests belonging to a suite with share enabled will always share exes.",
    )

    default = get_default_setting(config, "SINGLE_SUBMIT", False, check_main=False)

    parser.add_argument(
        "--single-submit",
        action="store_true",
        default=default,
        help="Use a single interactive allocation to run all the tests. This can "
        "\ndrastically reduce queue waiting but only makes sense on batch machines.",
    )

    default = get_default_setting(config, "TEST_ROOT", None, check_main=False)

    parser.add_argument(
        "-r",
        "--test-root",
        default=default,
        help="Where test cases will be created. The default is output root"
        "\nas defined in the config_machines file",
    )

    default = get_default_setting(config, "OUTPUT_ROOT", None, check_main=False)

    parser.add_argument(
        "--output-root", default=default, help="Where the case output is written."
    )

    default = get_default_setting(config, "BASELINE_ROOT", None, check_main=False)

    parser.add_argument(
        "--baseline-root",
        default=default,
        help="Specifies a root directory for baseline datasets that will "
        "\nbe used for Bit-for-bit generate and/or compare testing.",
    )

    default = get_default_setting(config, "CLEAN", False, check_main=False)

    parser.add_argument(
        "--clean",
        action="store_true",
        default=default,
        help="Specifies if tests should be cleaned after run. If set, all object"
        "\nexecutables and data files will be removed after the tests are run.",
    )

    default = get_default_setting(config, "MACHINE", None, check_main=True)

    parser.add_argument(
        "-m",
        "--machine",
        default=default,
        help="The machine for creating and building tests. This machine must be defined"
        "\nin the config_machines.xml file for the given model. The default is to "
        "\nto match the name of the machine in the test name or the name of the "
        "\nmachine this script is run on to the NODENAME_REGEX field in "
        "\nconfig_machines.xml. WARNING: This option is highly unsafe and should "
        "\nonly be used if you are an expert.",
    )

    default = get_default_setting(config, "MPILIB", None, check_main=True)

    parser.add_argument(
        "--mpilib",
        default=default,
        help="Specify the mpilib. To see list of supported MPI libraries for each machine, "
        "\ninvoke ./query_config. The default is the first listing .",
    )

    if model_config.create_test_flag_mode == "cesm":
        parser.add_argument(
            "-c",
            "--compare",
            help="While testing, compare baselines against the given compare directory. ",
        )

        parser.add_argument(
            "-g",
            "--generate",
            help="While testing, generate baselines in the given generate directory. "
            "\nNOTE: this can also be done after the fact with bless_test_results",
        )

        parser.add_argument(
            "--xml-machine",
            help="Use this machine key in the lookup in testlist.xml. "
            "\nThe default is all if any --xml- argument is used.",
        )

        parser.add_argument(
            "--xml-compiler",
            help="Use this compiler key in the lookup in testlist.xml. "
            "\nThe default is all if any --xml- argument is used.",
        )

        parser.add_argument(
            "--xml-category",
            help="Use this category key in the lookup in testlist.xml. "
            "\nThe default is all if any --xml- argument is used.",
        )

        parser.add_argument(
            "--xml-testlist",
            help="Use this testlist to lookup tests.The default is specified in config_files.xml",
        )

        parser.add_argument(
            "--driver",
            choices=model_config.driver_choices,
            help="Override driver specified in tests and use this one.",
        )

        parser.add_argument(
            "testargs",
            nargs="*",
            help="Tests to run. Testname form is TEST.GRID.COMPSET[.MACHINE_COMPILER]",
        )

    else:

        parser.add_argument(
            "testargs",
            nargs="+",
            help="Tests or test suites to run."
            " Testname form is TEST.GRID.COMPSET[.MACHINE_COMPILER]",
        )

        parser.add_argument(
            "-b",
            "--baseline-name",
            help="If comparing or generating baselines, use this directory under baseline root. "
            "\nDefault will be current branch name.",
        )

        parser.add_argument(
            "-c",
            "--compare",
            action="store_true",
            help="While testing, compare baselines",
        )

        parser.add_argument(
            "-g",
            "--generate",
            action="store_true",
            help="While testing, generate baselines. "
            "\nNOTE: this can also be done after the fact with bless_test_results",
        )

        parser.add_argument(
            "--driver",
            help="Override driver specified in tests and use this one.",
        )

    default = get_default_setting(config, "COMPILER", None, check_main=True)

    parser.add_argument(
        "--compiler",
        default=default,
        help="Compiler for building cime. Default will be the name in the "
        "\nTestname or the default defined for the machine.",
    )

    parser.add_argument(
        "-n",
        "--namelists-only",
        action="store_true",
        help="Only perform namelist actions for tests",
    )

    parser.add_argument(
        "-p",
        "--project",
        help="Specify a project id for the case (optional)."
        "\nUsed for accounting and directory permissions when on a batch system."
        "\nThe default is user or machine specified by PROJECT."
        "\nAccounting (only) may be overridden by user or machine specified CHARGE_ACCOUNT.",
    )

    parser.add_argument(
        "-t",
        "--test-id",
        help="Specify an 'id' for the test. This is simply a string that is appended "
        "\nto the end of a test name. If no test-id is specified, a time stamp plus a "
        "\nrandom string will be used (ensuring a high probability of uniqueness). "
        "\nIf a test-id is specified, it is the user's responsibility to ensure that "
        "\neach run of create_test uses a unique test-id. WARNING: problems will occur "
        "\nif you use the same test-id twice on the same file system, even if the test "
        "\nlists are completely different.",
    )

    default = get_default_setting(config, "PARALLEL_JOBS", None, check_main=False)

    parser.add_argument(
        "-j",
        "--parallel-jobs",
        type=int,
        default=default,
        help="Number of tasks create_test should perform simultaneously. The default "
        "\n is min(num_cores, num_tests).",
    )

    default = get_default_setting(config, "PROC_POOL", None, check_main=False)

    parser.add_argument(
        "--proc-pool",
        type=int,
        default=default,
        help="The size of the processor pool that create_test can use. The default is "
        "\nMAX_MPITASKS_PER_NODE + 25 percent.",
    )

    default = os.getenv("CIME_GLOBAL_WALLTIME")
    if default is None:
        default = get_default_setting(config, "WALLTIME", None, check_main=True)

    parser.add_argument(
        "--walltime",
        default=default,
        help="Set the wallclock limit for all tests in the suite. "
        "\nUse the variable CIME_GLOBAL_WALLTIME to set this for all tests.",
    )

    default = get_default_setting(config, "JOB_QUEUE", None, check_main=True)

    parser.add_argument(
        "-q",
        "--queue",
        default=default,
        help="Force batch system to use a certain queue",
    )

    parser.add_argument(
        "-f", "--testfile", help="A file containing an ascii list of tests to run"
    )

    default = get_default_setting(
        config, "ALLOW_BASELINE_OVERWRITE", False, check_main=False
    )

    default = get_default_setting(
        config, "SKIP_TESTS_WITH_EXISTING_BASELINES", False, check_main=False
    )

    # Don't allow -o/--allow-baseline-overwrite AND --skip-tests-with-existing-baselines
    existing_baseline_group = parser.add_mutually_exclusive_group()

    existing_baseline_group.add_argument(
        "--allow-baseline-overwrite",
        "-o",
        action="store_true",
        default=default,
        help="If the --generate option is given, then an attempt to overwrite "
        "\nan existing baseline directory will raise an error. WARNING: Specifying this "
        "\noption will allow existing baseline directories to be silently overwritten. "
        "\nIncompatible with --skip-tests-with-existing-baselines.",
    )

    existing_baseline_group.add_argument(
        "--skip-tests-with-existing-baselines",
        action="store_true",
        default=default,
        help="If the --generate option is given, then an attempt to overwrite "
        "\nan existing baseline directory will raise an error. WARNING: Specifying this "
        "\noption will allow tests with existing baseline directories to be silently skipped. "
        "\nIncompatible with -o/--allow-baseline-overwrite.",
    )

    default = get_default_setting(config, "WAIT", False, check_main=False)

    parser.add_argument(
        "--wait",
        action="store_true",
        default=default,
        help="On batch systems, wait for submitted jobs to complete",
    )

    default = get_default_setting(config, "ALLOW_PNL", False, check_main=False)

    parser.add_argument(
        "--allow-pnl",
        action="store_true",
        default=default,
        help="Do not pass skip-pnl to case.submit",
    )

    parser.add_argument(
        "--check-throughput",
        action="store_true",
        help="Fail if throughput check fails. Requires --wait on batch systems",
    )

    parser.add_argument(
        "--check-memory",
        action="store_true",
        help="Fail if memory check fails. Requires --wait on batch systems",
    )

    parser.add_argument(
        "--ignore-namelists",
        action="store_true",
        help="Do not fail if there namelist diffs",
    )

    parser.add_argument(
        "--ignore-diffs",
        action="store_true",
        help="Do not fail if there history file diffs",
    )

    parser.add_argument(
        "--ignore-memleak", action="store_true", help="Do not fail if there's a memleak"
    )

    default = get_default_setting(config, "FORCE_PROCS", None, check_main=False)

    parser.add_argument(
        "--force-procs",
        type=int,
        default=default,
        help="For all tests to run with this number of processors",
    )

    default = get_default_setting(config, "FORCE_THREADS", None, check_main=False)

    parser.add_argument(
        "--force-threads",
        type=int,
        default=default,
        help="For all tests to run with this number of threads",
    )

    default = get_default_setting(config, "INPUT_DIR", None, check_main=True)

    parser.add_argument(
        "-i",
        "--input-dir",
        default=default,
        help="Use a non-default location for input files",
    )

    default = get_default_setting(config, "PESFILE", None, check_main=True)

    parser.add_argument(
        "--pesfile",
        default=default,
        help="Full pathname of an optional pes specification file. The file"
        "\ncan follow either the config_pes.xml or the env_mach_pes.xml format.",
    )

    default = get_default_setting(config, "RETRY", 0, check_main=False)

    parser.add_argument(
        "--retry",
        type=int,
        default=default,
        help="Automatically retry failed tests. >0 implies --wait",
    )

    parser.add_argument(
        "-N",
        "--non-local",
        action="store_true",
        help="Use when you've requested a machine that you aren't on. "
        "Will reduce errors for missing directories etc.",
    )

    if config and config.has_option("main", "workflow"):
        workflow_default = config.get("main", "workflow")
    else:
        workflow_default = "default"

    parser.add_argument(
        "--workflow",
        default=workflow_default,
        help="A workflow from config_workflow.xml to apply to this case. ",
    )

    parser.add_argument(
        "--chksum", action="store_true", help="Verifies input data checksums."
    )

    srcroot_default = utils.get_src_root()

    parser.add_argument(
        "--srcroot",
        default=srcroot_default,
        help="Alternative pathname for source root directory. "
        f"The default is {srcroot_default}",
    )

    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="When used with 'use-existing' and 'test-id', the"
        "tests will have their 'BUILD_SHAREDLIB' phase reset to 'PEND'.",
    )

    CIME.utils.add_mail_type_args(parser)

    args = CIME.utils.parse_args_and_handle_standard_logging_options(args, parser)

    CIME.utils.resolve_mail_type_args(args)

    if args.force_rebuild:
        expect(
            args.use_existing and args.test_id,
            "Cannot force a rebuild without 'use-existing' and 'test-id'",
        )

    # generate and compare flags may not point to the same directory
    if model_config.create_test_flag_mode == "cesm":
        if args.generate is not None:
            expect(
                not (args.generate == args.compare),
                "Cannot generate and compare baselines at the same time",
            )

        if args.xml_testlist is not None:
            expect(
                not (
                    args.xml_machine is None
                    and args.xml_compiler is None
                    and args.xml_category is None
                ),
                "If an xml-testlist is present at least one of --xml-machine, "
                "--xml-compiler, --xml-category must also be present",
            )

    else:
        expect(
            not (
                args.baseline_name is not None
                and (not args.compare and not args.generate)
            ),
            "Provided baseline name but did not specify compare or generate",
        )
        expect(
            not (args.compare and args.generate),
            "Tried to compare and generate at same time",
        )

    expect(
        not (args.namelists_only and not (args.generate or args.compare)),
        "Must provide either --compare or --generate with --namelists-only",
    )

    if args.retry > 0:
        args.wait = True

    if args.parallel_jobs is not None:
        expect(
            args.parallel_jobs > 0,
            "Invalid value for parallel_jobs: %d" % args.parallel_jobs,
        )

    if args.use_existing:
        expect(args.test_id is not None, "Must provide test-id of pre-existing cases")

    if args.no_setup:
        args.no_build = True

    if args.no_build:
        args.no_run = True

    # Namelist-only forces some other options:
    if args.namelists_only:
        expect(not args.no_setup, "Cannot compare namelists without setup")
        args.no_build = True
        args.no_run = True
        args.no_batch = True

    expect(
        not (args.non_local and not args.no_build), "Cannot build on non-local machine"
    )

    if args.single_submit:
        expect(
            not args.no_run,
            "Doesn't make sense to request single-submit if no-run is on",
        )
        args.no_build = True
        args.no_run = True
        args.no_batch = True

    if args.test_id is None:
        args.test_id = "%s_%s" % (CIME.utils.get_timestamp(), CIME.utils.id_generator())
    else:
        expect(
            CIME.utils.check_name(args.test_id, additional_chars="."),
            "invalid test-id argument provided",
        )

    if args.testfile is not None:
        with open(args.testfile, "r") as fd:
            args.testargs.extend(
                [
                    line.strip()
                    for line in fd.read().splitlines()
                    if line.strip() and not line.startswith("#")
                ]
            )

    # Propagate `srcroot` to `GenericXML` to resolve $SRCROOT
    # See call to `Machines` below
    utils.GLOBAL["SRCROOT"] = args.srcroot

    # Compute list of fully-resolved test_names
    test_extra_data = {}
    if model_config.check_machine_name_from_test_name:
        machine_name = args.xml_machine if args.machine is None else args.machine

        # If it's still unclear what machine to use, look at test names
        if machine_name is None:
            for test in args.testargs:
                testsplit = CIME.utils.parse_test_name(test)
                if testsplit[4] is not None:
                    if machine_name is None:
                        machine_name = testsplit[4]
                    else:
                        expect(
                            machine_name == testsplit[4],
                            "ambiguity in machine, please use the --machine option",
                        )

        mach_obj = Machines(machine=machine_name)
        if args.testargs:
            args.compiler = (
                mach_obj.get_default_compiler()
                if args.compiler is None
                else args.compiler
            )
            test_names = get_tests.get_full_test_names(
                args.testargs, mach_obj.get_machine_name(), args.compiler
            )
        else:
            expect(
                not (
                    args.xml_machine is None
                    and args.xml_compiler is None
                    and args.xml_category is None
                    and args.xml_testlist is None
                ),
                "At least one of --xml-machine, --xml-testlist, "
                "--xml-compiler, --xml-category or a valid test name must be provided.",
            )

            test_data = get_tests_from_xml(
                xml_machine=args.xml_machine,
                xml_category=args.xml_category,
                xml_compiler=args.xml_compiler,
                xml_testlist=args.xml_testlist,
                machine=machine_name,
                compiler=args.compiler,
                driver=args.driver,
            )
            test_names = [item["name"] for item in test_data]
            for test_datum in test_data:
                test_extra_data[test_datum["name"]] = test_datum

        logger.info("Testnames: %s" % test_names)
    else:
        inf_machine, inf_compilers = get_tests.infer_arch_from_tests(args.testargs)
        if args.machine is None:
            args.machine = inf_machine

        mach_obj = Machines(machine=args.machine)
        if args.compiler is None:
            if len(inf_compilers) == 0:
                args.compiler = mach_obj.get_default_compiler()
            elif len(inf_compilers) == 1:
                args.compiler = inf_compilers[0]
            else:
                # User has multiple compiler specifications in their testargs
                args.compiler = inf_compilers[0]
                expect(
                    not args.compare and not args.generate,
                    "It is not safe to do baseline operations with heterogenous compiler set: {}".format(
                        inf_compilers
                    ),
                )

        test_names = get_tests.get_full_test_names(
            args.testargs, mach_obj.get_machine_name(), args.compiler
        )

    expect(
        mach_obj.is_valid_compiler(args.compiler),
        "Compiler %s not valid for machine %s"
        % (args.compiler, mach_obj.get_machine_name()),
    )

    if not args.wait and mach_obj.has_batch_system() and not args.no_batch:
        expect(
            not args.check_throughput,
            "Makes no sense to use --check-throughput without --wait",
        )
        expect(
            not args.check_memory, "Makes no sense to use --check-memory without --wait"
        )

    # Normalize compare/generate between the models
    baseline_cmp_name = None
    baseline_gen_name = None
    if args.compare or args.generate:
        if model_config.create_test_flag_mode == "cesm":
            if args.compare is not None:
                baseline_cmp_name = args.compare
            if args.generate is not None:
                baseline_gen_name = args.generate
        else:
            baseline_name = (
                args.baseline_name
                if args.baseline_name
                else CIME.utils.get_current_branch(repo=CIME.utils.get_cime_root())
            )
            expect(
                baseline_name is not None,
                "Could not determine baseline name from branch, please use -b option",
            )
            if args.compare:
                baseline_cmp_name = baseline_name
            elif args.generate:
                baseline_gen_name = baseline_name

    if args.input_dir is not None:
        args.input_dir = os.path.abspath(args.input_dir)

    # sanity check
    for name in test_names:
        dot_count = name.count(".")
        expect(dot_count > 1 and dot_count <= 4, "Invalid test Name, '{}'".format(name))

    # for e3sm, sort by walltime
    if model_config.sort_tests:
        if args.walltime is None:
            # Longest tests should run first
            test_names.sort(key=get_tests.key_test_time, reverse=True)
        else:
            test_names.sort()

    return (
        test_names,
        test_extra_data,
        args.compiler,
        mach_obj.get_machine_name(),
        args.no_run,
        args.no_build,
        args.no_setup,
        args.no_batch,
        args.test_root,
        args.baseline_root,
        args.clean,
        baseline_cmp_name,
        baseline_gen_name,
        args.namelists_only,
        args.project,
        args.test_id,
        args.parallel_jobs,
        args.walltime,
        args.single_submit,
        args.proc_pool,
        args.use_existing,
        args.save_timing,
        args.queue,
        args.allow_baseline_overwrite,
        args.skip_tests_with_existing_baselines,
        args.output_root,
        args.wait,
        args.force_procs,
        args.force_threads,
        args.mpilib,
        args.input_dir,
        args.pesfile,
        args.retry,
        args.mail_user,
        args.mail_type,
        args.check_throughput,
        args.check_memory,
        args.ignore_namelists,
        args.ignore_diffs,
        args.ignore_memleak,
        args.allow_pnl,
        args.non_local,
        args.single_exe,
        args.workflow,
        args.chksum,
        args.force_rebuild,
        args.driver,
    )


###############################################################################
def get_default_setting(config, varname, default_if_not_found, check_main=False):
    ###############################################################################
    if config.has_option("create_test", varname):
        default = config.get("create_test", varname)
    elif check_main and config.has_option("main", varname):
        default = config.get("main", varname)
    else:
        default = default_if_not_found
    return default


###############################################################################
def single_submit_impl(
    machine_name, test_id, proc_pool, _, args, job_cost_map, wall_time, test_root
):
    ###############################################################################
    mach = Machines(machine=machine_name)
    expect(
        mach.has_batch_system(),
        "Single submit does not make sense on non-batch machine '%s'"
        % mach.get_machine_name(),
    )

    machine_name = mach.get_machine_name()

    #
    # Compute arg list for second call to create_test
    #
    new_args = list(args)
    new_args.remove("--single-submit")
    new_args.append("--no-batch")
    new_args.append("--use-existing")
    no_arg_is_a_test_id_arg = True
    no_arg_is_a_proc_pool_arg = True
    no_arg_is_a_machine_arg = True
    for arg in new_args:
        if arg == "-t" or arg.startswith("--test-id"):
            no_arg_is_a_test_id_arg = False
        elif arg.startswith("--proc-pool"):
            no_arg_is_a_proc_pool_arg = False
        elif arg == "-m" or arg.startswith("--machine"):
            no_arg_is_a_machine_arg = True

    if no_arg_is_a_test_id_arg:
        new_args.append("-t %s" % test_id)
    if no_arg_is_a_proc_pool_arg:
        new_args.append("--proc-pool %d" % proc_pool)
    if no_arg_is_a_machine_arg:
        new_args.append("-m %s" % machine_name)

    #
    # Resolve batch directives manually. There is currently no other way
    # to do this without making a Case object. Make a throwaway case object
    # to help us here.
    #
    testcase_dirs = glob.glob("%s/*%s*/TestStatus" % (test_root, test_id))
    expect(testcase_dirs, "No test case dirs found!?")
    first_case = os.path.abspath(os.path.dirname(testcase_dirs[0]))
    with Case(first_case, read_only=False) as case:
        env_batch = case.get_env("batch")

        submit_cmd = env_batch.get_value("batch_submit", subgroup=None)
        submit_args = env_batch.get_submit_args(case, "case.test")

    tasks_per_node = mach.get_value("MAX_MPITASKS_PER_NODE")
    num_nodes = int(math.ceil(float(proc_pool) / tasks_per_node))
    if wall_time is None:
        wall_time = compute_total_time(job_cost_map, proc_pool)
        wall_time_bab = convert_to_babylonian_time(int(wall_time))
    else:
        wall_time_bab = wall_time

    queue = env_batch.select_best_queue(num_nodes, proc_pool, walltime=wall_time_bab)
    wall_time_max_bab = env_batch.get_queue_specs(queue)[3]
    if wall_time_max_bab is not None:
        wall_time_max = convert_to_seconds(wall_time_max_bab)
        if wall_time_max < wall_time:
            wall_time = wall_time_max
            wall_time_bab = convert_to_babylonian_time(wall_time)

    overrides = {
        "job_id": "create_test_single_submit_%s" % test_id,
        "num_nodes": num_nodes,
        "tasks_per_node": tasks_per_node,
        "totaltasks": tasks_per_node * num_nodes,
        "job_wallclock_time": wall_time_bab,
        "job_queue": env_batch.text(queue),
    }

    directives = env_batch.get_batch_directives(case, "case.test", overrides=overrides)

    #
    # Make simple submit script and submit
    #

    script = "#! /bin/bash\n"
    script += "\n%s" % directives
    script += "\n"
    script += "cd %s\n" % os.getcwd()
    script += "%s %s\n" % (__file__, " ".join(new_args))

    submit_cmd = "%s %s" % (submit_cmd, submit_args)
    logger.info("Script:\n%s" % script)

    run_cmd_no_fail(
        submit_cmd, input_str=script, arg_stdout=None, arg_stderr=None, verbose=True
    )


###############################################################################
# pragma pylint: disable=protected-access
def create_test(
    test_names,
    test_data,
    compiler,
    machine_name,
    no_run,
    no_build,
    no_setup,
    no_batch,
    test_root,
    baseline_root,
    clean,
    baseline_cmp_name,
    baseline_gen_name,
    namelists_only,
    project,
    test_id,
    parallel_jobs,
    walltime,
    single_submit,
    proc_pool,
    use_existing,
    save_timing,
    queue,
    allow_baseline_overwrite,
    skip_tests_with_existing_baselines,
    output_root,
    wait,
    force_procs,
    force_threads,
    mpilib,
    input_dir,
    pesfile,
    run_count,
    mail_user,
    mail_type,
    check_throughput,
    check_memory,
    ignore_namelists,
    ignore_diffs,
    ignore_memleak,
    allow_pnl,
    non_local,
    single_exe,
    workflow,
    chksum,
    force_rebuild,
    driver,
):
    ###############################################################################
    impl = TestScheduler(
        test_names,
        test_data=test_data,
        no_run=no_run,
        no_build=no_build,
        no_setup=no_setup,
        no_batch=no_batch,
        test_root=test_root,
        test_id=test_id,
        baseline_root=baseline_root,
        baseline_cmp_name=baseline_cmp_name,
        baseline_gen_name=baseline_gen_name,
        clean=clean,
        machine_name=machine_name,
        compiler=compiler,
        namelists_only=namelists_only,
        project=project,
        parallel_jobs=parallel_jobs,
        walltime=walltime,
        proc_pool=proc_pool,
        use_existing=use_existing,
        save_timing=save_timing,
        queue=queue,
        allow_baseline_overwrite=allow_baseline_overwrite,
        skip_tests_with_existing_baselines=skip_tests_with_existing_baselines,
        output_root=output_root,
        force_procs=force_procs,
        force_threads=force_threads,
        mpilib=mpilib,
        input_dir=input_dir,
        pesfile=pesfile,
        run_count=run_count,
        mail_user=mail_user,
        mail_type=mail_type,
        allow_pnl=allow_pnl,
        non_local=non_local,
        single_exe=single_exe,
        workflow=workflow,
        chksum=chksum,
        force_rebuild=force_rebuild,
        driver=driver,
    )

    success = impl.run_tests(
        wait=wait,
        check_throughput=check_throughput,
        check_memory=check_memory,
        ignore_namelists=ignore_namelists,
        ignore_diffs=ignore_diffs,
        ignore_memleak=ignore_memleak,
    )

    if success and single_submit:
        # Get real test root
        test_root = impl._test_root

        job_cost_map = {}
        largest_case = 0
        for test in impl._tests:
            test_dir = impl._get_test_dir(test)
            procs_needed = impl._get_procs_needed(test, RUN_PHASE)
            time_needed = convert_to_seconds(
                run_cmd_no_fail(
                    "./xmlquery JOB_WALLCLOCK_TIME -value -subgroup case.test",
                    from_dir=test_dir,
                )
            )
            job_cost_map[test] = (procs_needed, time_needed)
            if procs_needed > largest_case:
                largest_case = procs_needed

        if proc_pool is None:
            # Based on size of created jobs, choose a reasonable proc_pool. May need to put
            # more thought into this.
            proc_pool = 2 * largest_case

        # Create submit script
        single_submit_impl(
            machine_name,
            test_id,
            proc_pool,
            project,
            sys.argv[1:],
            job_cost_map,
            walltime,
            test_root,
        )

    return success


###############################################################################
def _main_func(description=None):
    ###############################################################################
    customize_path = os.path.join(utils.get_src_root(), "cime_config", "customize")

    if os.path.exists(customize_path):
        Config.instance().load(customize_path)

    (
        test_names,
        test_data,
        compiler,
        machine_name,
        no_run,
        no_build,
        no_setup,
        no_batch,
        test_root,
        baseline_root,
        clean,
        baseline_cmp_name,
        baseline_gen_name,
        namelists_only,
        project,
        test_id,
        parallel_jobs,
        walltime,
        single_submit,
        proc_pool,
        use_existing,
        save_timing,
        queue,
        allow_baseline_overwrite,
        skip_tests_with_existing_baselines,
        output_root,
        wait,
        force_procs,
        force_threads,
        mpilib,
        input_dir,
        pesfile,
        retry,
        mail_user,
        mail_type,
        check_throughput,
        check_memory,
        ignore_namelists,
        ignore_diffs,
        ignore_memleak,
        allow_pnl,
        non_local,
        single_exe,
        workflow,
        chksum,
        force_rebuild,
        driver,
    ) = parse_command_line(sys.argv, description)

    success = False
    run_count = 0
    while not success and run_count <= retry:
        use_existing = use_existing if run_count == 0 else True
        allow_baseline_overwrite = allow_baseline_overwrite if run_count == 0 else True
        success = create_test(
            test_names,
            test_data,
            compiler,
            machine_name,
            no_run,
            no_build,
            no_setup,
            no_batch,
            test_root,
            baseline_root,
            clean,
            baseline_cmp_name,
            baseline_gen_name,
            namelists_only,
            project,
            test_id,
            parallel_jobs,
            walltime,
            single_submit,
            proc_pool,
            use_existing,
            save_timing,
            queue,
            allow_baseline_overwrite,
            skip_tests_with_existing_baselines,
            output_root,
            wait,
            force_procs,
            force_threads,
            mpilib,
            input_dir,
            pesfile,
            run_count,
            mail_user,
            mail_type,
            check_throughput,
            check_memory,
            ignore_namelists,
            ignore_diffs,
            ignore_memleak,
            allow_pnl,
            non_local,
            single_exe,
            workflow,
            chksum,
            force_rebuild,
            driver,
        )
        run_count += 1

        # For testing only
        os.environ["TESTBUILDFAIL_PASS"] = "True"
        os.environ["TESTRUNFAIL_PASS"] = "True"

    sys.exit(0 if success else CIME.utils.TESTS_FAILED_ERR_CODE)


###############################################################################

if __name__ == "__main__":
    _main_func(__doc__)
