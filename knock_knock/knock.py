#!/usr/bin/env python3

import argparse
import logging
import multiprocessing
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml
import tqdm

import knock_knock
import knock_knock.build_targets
import knock_knock.experiment
import knock_knock.table

def check_blastn(require_precise_version=False):
    try:
        output = subprocess.check_output(['blastn', '-version'])
        if require_precise_version and b'2.7.1' not in output:
            print('blastn 2.7.1 is required and couldn\'t be found')
            sys.exit(1)
    except:
        print('blastn is required and couldn\'t be found')
        sys.exit(1)

def parallel(args):
    check_blastn()

    if args.group:
        args.conditions['batch'] = args.group

    exps = knock_knock.experiment.get_all_experiments(args.project_directory, args.conditions)

    if len(exps) == 0:
        print('No experiments satify conditions:')
        print(args.conditions)
        sys.exit(1)

    def process_stage(stage):
        with multiprocessing.Pool(processes=args.max_procs, maxtasksperchild=1) as process_pool:
            arg_tuples = []

            for _, exp in exps.items():
                arg_tuple = (exp.base_dir, exp.batch, exp.sample_name, stage, args.progress, True)
                arg_tuples.append(arg_tuple)

            process_pool.starmap(knock_knock.experiment.process_experiment_stage, arg_tuples)

    stages = args.stages.split(',')
    for stage in stages:
        process_stage(stage)

def process(args):
    check_blastn()

    stages = args.stages.split(',')

    for stage in stages:
        knock_knock.experiment.process_experiment_stage(args.project_directory,
                                                        args.group,
                                                        args.sample,
                                                        stage,
                                                        progress=args.progress,
                                                        print_timestamps=True,
                                                       )

def make_tables(args):
    results_dir = args.project_directory / 'results'

    if args.group:
        groups = args.group.split(',')

        conditions = {
            'batch': groups,
        }

        if args.experiment_type is not None:
            conditions['experiment_type'] = args.experiment_type

        knock_knock.table.make_self_contained_zip(args.project_directory,
                                                  conditions,
                                                  args.title,
                                                  sort_samples=not args.unsorted,
                                                  arrayed=args.arrayed,
                                                  vmax_multiple=args.vmax_multiple,
                                                 )
    else:
        groups = knock_knock.experiment.get_all_batches(args.project_directory)

        #results_dir = args.project_directory / 'results'
        #csv_fn = (results_dir / 'all_groups').with_suffix('.csv')
        #df = knock_knock.table.load_counts(args.project_directory, exclude_empty=False, arrayed=args.arrayed).T
        #df.to_csv(csv_fn)

        for group in groups:
            logging.info(f'Making {group}')

            conditions = {'batch': group}
            if args.experiment_type is not None:
                conditions['experiment_type'] = args.experiment_type

            knock_knock.table.make_self_contained_zip(args.project_directory,
                                                      conditions,
                                                      group,
                                                      sort_samples=not args.unsorted,
                                                      arrayed=args.arrayed,
                                                      vmax_multiple=args.vmax_multiple,
                                                     )

def build_targets(args):
    knock_knock.build_targets.build_target_infos_from_csv(args.project_directory,
                                                          offtargets=args.offtargets,
                                                          defer_HA_identification=args.defer_HA_identification,
                                                         )

def build_manual_target(args):
    knock_knock.build_targets.build_manual_target(args.project_directory, args.target_name)

def design_primers(args):
    knock_knock.build_targets.design_amplicon_primers_from_csv(args.project_directory, args.genome)

def build_indices(args):
    knock_knock.build_targets.download_genome_and_build_indices(args.project_directory,
                                                                args.genome_name,
                                                                args.num_threads
                                                               )

def install_example_data(args):
    package_dir = Path(os.path.realpath(knock_knock.__file__)).parent
    subdirs_to_copy = ['data', 'targets']
    for subdir in subdirs_to_copy:
        src = package_dir / 'example_data' / subdir
        dest = args.project_directory / subdir

        if dest.exists():
            print(f'Can\'t install to {args.project_directory}, {dest} already exists')
            sys.exit(1)

        shutil.copytree(str(src), str(dest))

    logging.info(f'Example data installed in {args.project_directory}')

def print_citation(args):
    citation = '''
        Hera Canaj, Jeffrey A. Hussmann, Han Li, Kyle A. Beckman, Leeanne Goodrich,
        Nathan H. Cho, Yucheng J. Li, Daniel A Santos, Aaron McGeever, Edna M Stewart,
        Veronica Pessino, Mohammad A Mandegar, Cindy Huang, Li Gan, Barbara Panning,
        Bo Huang, Jonathan S. Weissman and Manuel D. Leonetti.  "Deep profiling reveals
        the complexity of integration outcomes in CRISPR knock-in experiments."
        https://www.biorxiv.org/content/10.1101/841098v1 (2019).
    '''
    print(textwrap.dedent(citation))

def main():
    logging.basicConfig(format='%(asctime)s: %(message)s',
                        datefmt='%y-%m-%d %H:%M:%S',
                        level=logging.INFO,
                       )

    parser = argparse.ArgumentParser(prog='knock-knock')

    parser.add_argument('--version', action='version', version=knock_knock.__version__)

    subparsers = parser.add_subparsers(dest='subcommand', title='subcommands')
    subparsers.required = True

    def add_project_directory_arg(parser):
        parser.add_argument('project_directory', type=Path, help='the base directory to store input data, reference annotations, and analysis output for a project')

    parser_process = subparsers.add_parser('process', help='process a single sample')
    add_project_directory_arg(parser_process)
    parser_process.add_argument('group', help='group name')
    parser_process.add_argument('sample', help='sample name')
    parser_process.add_argument('--progress', const=tqdm.tqdm, action='store_const', help='show progress bars')
    parser_process.add_argument('--stages', default='preprocess,align,categorize,visualize')
    parser_process.set_defaults(func=process)

    parser_parallel = subparsers.add_parser('parallel', help='process multiple samples in parallel')
    add_project_directory_arg(parser_parallel)
    parser_parallel.add_argument('max_procs', type=int, help='maximum number of samples to process at once')
    parser_parallel.add_argument('--group', help='if specified, the single group name to process; if not specified, all groups will be processed')
    parser_parallel.add_argument('--conditions', type=yaml.safe_load, default={}, help='if specified, conditions that samples must satisfy to be processed, given as yaml; if not specified, all samples will be processed')
    parser_parallel.add_argument('--stages', default='preprocess,align,categorize,visualize')
    parser_parallel.add_argument('--progress', const=tqdm.tqdm, action='store_const', help='show progress bars')
    parser_parallel.set_defaults(func=parallel)

    parser_table = subparsers.add_parser('table', help='generate tables of outcome frequencies')
    add_project_directory_arg(parser_table)
    parser_table.add_argument('--group', help='if specified, a comma-separated list groups to include; if not specified, all groups will be generated')
    parser_table.add_argument('--title', default='knock_knock_table', help='if specified, a title for output files')
    parser_table.add_argument('--unsorted', action='store_true', help='don\'t sort samples')
    parser_table.add_argument('--arrayed', action='store_true', help='samples are organized as arrayed_experiment_groups')
    parser_table.add_argument('--vmax_multiple', type=float, default=1, help='fractional value that corresponds to full horizontal bar')
    parser_table.add_argument('--experiment_type', help='experiment type to include')
    parser_table.set_defaults(func=make_tables)

    parser_targets = subparsers.add_parser('build-targets', help='build annotations of target locii')
    add_project_directory_arg(parser_targets)
    parser_targets.add_argument('--offtargets', action='store_true', help='don\'t enforce PAMs')
    parser_targets.add_argument('--defer_HA_identification', action='store_true', help='don\'t try to identiy homology arms')
    parser_targets.set_defaults(func=build_targets)

    parser_manual_target = subparsers.add_parser('build-manual-target', help='build a single target from a hand-annotated genbank file')
    add_project_directory_arg(parser_manual_target)
    parser_manual_target.add_argument('target_name', help='sample name')
    parser_manual_target.set_defaults(func=build_manual_target)

    parser_primers = subparsers.add_parser('design-primers', help='design amplicon primers for sgRNAs')
    add_project_directory_arg(parser_primers)
    parser_primers.add_argument('--genome', default='hg19')
    parser_primers.set_defaults(func=design_primers)

    parser_indices = subparsers.add_parser('build-indices', help='download a reference genome and build alignment indices')
    add_project_directory_arg(parser_indices)
    parser_indices.add_argument('genome_name', help='name of genome to download')
    parser_indices.add_argument('--num-threads', type=int, default=8, help='number of threads to use for index building')
    parser_indices.set_defaults(func=build_indices)

    parser_install_data = subparsers.add_parser('install-example-data', help='install example data into user-specified project directory')
    add_project_directory_arg(parser_install_data)
    parser_install_data.set_defaults(func=install_example_data)

    parser_citation = subparsers.add_parser('whos-there', help='print citation information')
    parser_citation.set_defaults(func=print_citation)

    args = parser.parse_args()
    args.func(args)
