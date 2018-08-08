#!/usr/bin/env python3
from mob_suite.version import __version__
from collections import OrderedDict
import logging, os, shutil, sys, operator
from subprocess import Popen, PIPE
from argparse import (ArgumentParser, FileType)
from mob_suite.blast import BlastRunner
from mob_suite.blast import BlastReader
from mob_suite.wrappers import circlator
from mob_suite.wrappers import mash
from mob_suite.classes.mcl import mcl
from mob_suite.utils import \
    fixStart, \
    read_fasta_dict, \
    write_fasta_dict, \
    filter_overlaping_records, \
    replicon_blast, \
    mob_blast, \
    repetitive_blast, \
    getRepliconContigs, \
    fix_fasta_header, \
    getMashBestHit, \
    verify_init, \
    check_dependencies

LOG_FORMAT = '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'


def parse_args():
    "Parse the input arguments, use '-h' for help"
    parser = ArgumentParser(
        description="Mob Suite: Typing and reconstruction of plasmids from draft and complete assemblies version: {}".format(
            __version__))
    parser.add_argument('-o', '--outdir', type=str, required=True, help='Output Directory to put results')
    parser.add_argument('-i', '--infile', type=str, required=True, help='Input assembly fasta file to process')
    parser.add_argument('-n', '--num_threads', type=int, required=False, help='Number of threads to be used', default=1)

    parser.add_argument('--min_rep_evalue', type=str, required=False,
                        help='Minimum evalue threshold for replicon blastn',
                        default=0.00001)
    parser.add_argument('--min_mob_evalue', type=str, required=False,
                        help='Minimum evalue threshold for relaxase tblastn',
                        default=0.00001)
    parser.add_argument('--min_con_evalue', type=str, required=False, help='Minimum evalue threshold for contig blastn',
                        default=0.00001)
    parser.add_argument('--min_rpp_evalue', type=str, required=False,
                        help='Minimum evalue threshold for repetitve elements blastn',
                        default=0.00001)

    parser.add_argument('--min_length', type=str, required=False, help='Minimum length of contigs to classify',
                        default=1000)

    parser.add_argument('--min_rep_ident', type=int, required=False, help='Minimum sequence identity for replicons',
                        default=80)
    parser.add_argument('--min_mob_ident', type=int, required=False, help='Minimum sequence identity for relaxases',
                        default=80)
    parser.add_argument('--min_con_ident', type=int, required=False, help='Minimum sequence identity for contigs',
                        default=80)
    parser.add_argument('--min_rpp_ident', type=int, required=False,
                        help='Minimum sequence identity for repetitive elements', default=80)

    parser.add_argument('--min_rep_cov', type=int, required=False,
                        help='Minimum percentage coverage of replicon query by input assembly',
                        default=80)

    parser.add_argument('--min_mob_cov', type=int, required=False,
                        help='Minimum percentage coverage of relaxase query by input assembly',
                        default=80)

    parser.add_argument('--min_con_cov', type=int, required=False,
                        help='Minimum percentage coverage of assembly contig by the plasmid reference database to be considered',
                        default=65)

    parser.add_argument('--min_rpp_cov', type=int, required=False,
                        help='Minimum percentage coverage of contigs by repetitive elements',
                        default=80)

    parser.add_argument('--min_overlap', type=int, required=False,
                        help='Minimum overlap of fragments',
                        default=10)

    parser.add_argument('-u', '--unicycler_contigs', required=False,
                        help='Check for circularity flag generated by unicycler in fasta headers', action='store_true')

    parser.add_argument('-c', '--run_circlator', required=False,
                        help='Run circlator minums2 pipeline to check for circular contigs', action='store_true')

    parser.add_argument('-k', '--keep_tmp', required=False, help='Do not delete temporary file directory',
                        action='store_true')

    parser.add_argument('-t', '--run_typer', required=False,
                        help='Automatically run Mob-typer on the identified plasmids',
                        action='store_true')

    parser.add_argument('--debug', required=False, help='Show debug information', action='store_true')

    parser.add_argument('--plasmid_db', type=str, required=False, help='Reference Database of complete plasmids',
                        default=os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             'databases/ncbi_plasmid_full_seqs.fas'))
    parser.add_argument('--plasmid_mash_db', type=str, required=False,
                        help='Companion Mash database of reference database',
                        default=os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             'databases/ncbi_plasmid_full_seqs.fas.msh'))
    parser.add_argument('--plasmid_db_type', type=str, required=False, help='Blast database type of reference database',
                        default='blastn')
    parser.add_argument('--plasmid_replicons', type=str, required=False, help='Fasta of plasmid replicons',
                        default=os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             'databases/rep.dna.fas'))
    parser.add_argument('--repetitive_mask', type=str, required=False, help='Fasta of known repetitive elements',
                        default=os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             'databases/repetitive.dna.fas'))
    parser.add_argument('--plasmid_mob', type=str, required=False, help='Fasta of plasmid relaxases',
                        default=os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             'databases/mob.proteins.faa'))

    return parser.parse_args()


def init_console_logger(lvl):
    logging_levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    report_lvl = logging_levels[lvl]

    logging.basicConfig(format=LOG_FORMAT, level=report_lvl)


def mcl_predict(blast_results_file, min_ident, min_cov, evalue, min_length, tmp_dir):
    if os.path.getsize(blast_results_file) == 0:
        return dict()

    blast_df = BlastReader(blast_results_file).df
    blast_df = blast_df.loc[blast_df['length'] >= min_length]
    blast_df = blast_df.loc[blast_df['qlen'] <= 400000]
    blast_df = blast_df.loc[blast_df['qlen'] >= min_length]
    blast_df = blast_df.loc[blast_df['qcovs'] >= min_cov]
    blast_df = blast_df.loc[blast_df['qlen'] >= min_length]
    blast_df = blast_df.reset_index(drop=True)
    for index, row in blast_df.iterrows():
        (seqid, clust_id) = row[1].split('|')
        blast_df.iloc[index, blast_df.columns.get_loc('sseqid')] = clust_id

    filtered_blast = os.path.join(tmp_dir, 'filtered_mcl_blast.txt')
    blast_df.to_csv(filtered_blast, sep='\t', header=False, line_terminator='\n', index=False)
    mcl_clusters = mcl(filtered_blast, tmp_dir).getclusters()

    return mcl_clusters


def run_mob_typer(fasta_path, outdir, num_threads=1):
    mob_typer_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'mob_typer.py')
    p = Popen(['python', mob_typer_path,
               '--infile', fasta_path,
               '--outdir', outdir,
               '--keep_tmp',
               '--num_threads', str(num_threads)],
              stdout=PIPE,
              stderr=PIPE)
    p.wait()
    stdout = p.stdout.read()
    stderr = p.stderr.read()

    return stdout.decode("utf-8")


def contig_blast(input_fasta, plasmid_db, min_ident, min_cov, evalue, min_length, tmp_dir, blast_results_file,
                 num_threads=1, word_size=11):
    blast_runner = None
    filtered_blast = os.path.join(tmp_dir, 'filtered_blast.txt')
    blast_runner = BlastRunner(input_fasta, tmp_dir)
    blast_runner.run_blast(query_fasta_path=input_fasta, blast_task='megablast', db_path=plasmid_db,
                           db_type='nucl', min_cov=min_cov, min_ident=min_ident, evalue=evalue,
                           blast_outfile=blast_results_file, num_threads=num_threads, word_size=11)
    if os.path.getsize(blast_results_file) == 0:
        fh = open(filtered_blast, 'w')
        fh.write('')
        fh.close()
        return dict()
    blast_df = BlastReader(blast_results_file).df
    blast_df = blast_df.loc[blast_df['length'] >= min_length]
    blast_df = blast_df.loc[blast_df['qlen'] <= 400000]
    blast_df = blast_df.loc[blast_df['qlen'] >= min_length]
    blast_df = blast_df.loc[blast_df['qcovs'] >= min_cov]
    blast_df = blast_df.loc[blast_df['qlen'] >= min_length]
    blast_df = blast_df.reset_index(drop=True)
    blast_df.to_csv(filtered_blast, sep='\t', header=False, line_terminator='\n', index=False)


def contig_blast_group(blast_results_file, overlap_threshold):
    if os.path.getsize(blast_results_file) == 0:
        return dict()
    blast_df = BlastReader(blast_results_file).df
    blast_df = blast_df.sort_values(['sseqid', 'sstart', 'send', 'bitscore'], ascending=[True, True, True, False])

    blast_df = filter_overlaping_records(blast_df, overlap_threshold, 'sseqid', 'sstart', 'send', 'bitscore')
    size = str(len(blast_df))
    prev_size = 0
    while size != prev_size:
        blast_df = filter_overlaping_records(blast_df, overlap_threshold, 'sseqid', 'sstart', 'send', 'bitscore')
        prev_size = size
        size = str(len(blast_df))

    cluster_scores = dict()
    groups = dict()
    hits = dict()
    contigs = dict()
    for index, row in blast_df.iterrows():
        query = row['qseqid']
        pID, clust_id = row['sseqid'].split('|')
        score = row['bitscore']
        pLen = row['slen']
        contig_id = row['qseqid']

        if not pID in hits:
            hits[pID] = {'score': 0, 'length': pLen, 'covered_bases': 0, 'clust_id': clust_id}

        if not clust_id in cluster_scores:
            cluster_scores[clust_id] = score
        elif score > cluster_scores[clust_id]:
            cluster_scores[clust_id] = score

        if not clust_id in groups:
            groups[clust_id] = dict()

        if not query in groups[clust_id]:
            groups[clust_id][query] = dict()

        if not contig_id in contigs:
            contigs[contig_id] = dict()

        if not clust_id in contigs[contig_id]:
            contigs[contig_id][clust_id] = 0

        if contigs[contig_id][clust_id] < score:
            contigs[contig_id][clust_id] = score

        groups[clust_id][query][contig_id] = score

        hits[pID]['score'] += score
        hits[pID]['covered_bases'] += score

    sorted_d = OrderedDict(sorted(iter(list(cluster_scores.items())), key=lambda x: x[1], reverse=True))

    for clust_id in sorted_d:
        score = sorted_d[clust_id]
        for contig_id in contigs:
            if clust_id in contigs[contig_id]:
                contigs[contig_id] = {clust_id: contigs[contig_id][clust_id]}

    return contigs


def circularize(input_fasta, output_prefix):
    c = circlator()
    c.run_minimus(input_fasta, output_prefix)
    clist = c.parse_minimus(output_prefix + '.log')
    cdict = dict()
    for c in clist:
        cdict[c] = ''
    return cdict


def main():

    args = parse_args()

    if args.debug:
        init_console_logger(3)
    logging.info("MOB-recon v. {} ".format(__version__))

    if not args.outdir:
        logging.error('Error, no output directory specified, please specify one')
        sys.exit(-1)

    if not args.infile:
        logging.error('Error, no fasta specified, please specify one')
        sys.exit(-1)

    if not os.path.isfile(args.infile):
        logging.error('Error, input fasta file does not exist: "{}"'.format(args.infile))
        sys.exit(-1)

    logging.info('Processing fasta file {}'.format(args.infile))
    logging.info('Analysis directory {}'.format(args.outdir))

    if not os.path.isdir(args.outdir):
        os.mkdir(args.outdir, 0o755)

    # Check that the needed databases have been initialized
    verify_init(logging)
    status_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'databases/status.txt')


    if not os.path.isfile(status_file):
        logging.error('Error needed databases have not been initialize please run mob_init and try again')
        sys.exit(-1)

    plasmid_files = dict()
    input_fasta = args.infile
    out_dir = args.outdir
    num_threads = args.num_threads
    tmp_dir = os.path.join(out_dir, '__tmp')
    file_id = os.path.basename(input_fasta)
    fixed_fasta = os.path.join(tmp_dir, 'fixed.input.fasta')
    chromosome_file = os.path.join(out_dir, 'chromosome.fasta')
    replicon_blast_results = os.path.join(tmp_dir, 'replicon_blast_results.txt')
    mob_blast_results = os.path.join(tmp_dir, 'mobrecon_blast_results.txt')
    repetitive_blast_results = os.path.join(tmp_dir, 'repetitive_blast_results.txt')
    contig_blast_results = os.path.join(tmp_dir, 'contig_blast_results.txt')


    # Input numeric params
    min_rep_ident = float(args.min_rep_ident)
    min_mob_ident = float(args.min_mob_ident)
    min_con_ident = float(args.min_con_ident)
    min_rpp_ident = float(args.min_rpp_ident)

    idents = {'min_rep_ident': min_rep_ident, 'min_mob_ident': min_mob_ident, 'min_con_ident': min_con_ident,
              'min_rpp_ident': min_rpp_ident}

    for param in idents:
        value = float(idents[param])
        if value < 60:
            logging.error("Error: {} is too low, please specify an integer between 70 - 100".format(param))
            sys.exit(-1)
        if value > 100:
            logging.error("Error: {} is too high, please specify an integer between 70 - 100".format(param))
            sys.exit(-1)

    min_rep_cov = float(args.min_rep_cov)
    min_mob_cov = float(args.min_mob_cov)
    min_con_cov = float(args.min_con_cov)
    min_rpp_cov = float(args.min_rpp_cov)

    covs = {'min_rep_cov': min_rep_cov, 'min_mob_cov': min_mob_cov, 'min_con_cov': min_con_cov,
            'min_rpp_cov': min_rpp_cov}

    for param in covs:
        value = float(covs[param])
        if value < 60:
            logging.error("Error: {} is too low, please specify an integer between 50 - 100".format(param))
            sys.exit(-1)
        if value > 100:
            logging.error("Error: {} is too high, please specify an integer between 50 - 100".format(param))
            sys.exit(-1)

    min_rep_evalue = float(args.min_rep_evalue)
    min_mob_evalue = float(args.min_mob_evalue)
    min_con_evalue = float(args.min_con_evalue)
    min_rpp_evalue = float(args.min_rpp_evalue)

    evalues = {'min_rep_evalue': min_rep_evalue, 'min_mob_evalue': min_mob_evalue, 'min_con_evalue': min_con_evalue,
               'min_rpp_evalue': min_rpp_evalue}

    for param in evalues:
        value = float(evalues[param])
        if value > 1:
            logging.error("Error: {} is too high, please specify an float evalue between 0 to 1".format(param))
            sys.exit(-1)

    min_overlapp = int(args.min_overlap)

    min_length = int(args.min_length)



    # Input numeric params
    min_rep_ident = float(args.min_rep_ident)
    min_mob_ident = float(args.min_mob_ident)
    min_con_ident = float(args.min_con_ident)
    min_rpp_ident = float(args.min_rpp_ident)

    idents = {'min_rep_ident': min_rep_ident, 'min_mob_ident': min_mob_ident, 'min_con_ident': min_con_ident,
              'min_rpp_ident': min_rpp_ident}

    for param in idents:
        value = idents[param]
        if value < 60:
            logging.error("Error: {} is too low, please specify an integer between 70 - 100".format(param))
            sys.exit(-1)
        if value > 100:
            logging.error("Error: {} is too high, please specify an integer between 70 - 100".format(param))
            sys.exit(-1)

    min_rep_cov = float(args.min_rep_cov)
    min_mob_cov = float(args.min_mob_cov)
    min_con_cov = float(args.min_con_cov)
    min_rpp_cov = float(args.min_rpp_cov)

    covs = {'min_rep_cov': min_rep_cov, 'min_mob_cov': min_mob_cov, 'min_con_cov': min_con_cov,
            'min_rpp_cov': min_rpp_cov}

    for param in covs:
        value = covs[param]
        if value < 60:
            logging.error("Error: {} is too low, please specify an integer between 50 - 100".format(param))
            sys.exit(-1)
        if value > 100:
            logging.error("Error: {} is too high, please specify an integer between 50 - 100".format(param))
            sys.exit(-1)

    min_rep_evalue = float(args.min_rep_evalue)
    min_mob_evalue = float(args.min_mob_evalue)
    min_con_evalue = float(args.min_con_evalue)
    min_rpp_evalue = float(args.min_rpp_evalue)

    evalues = {'min_rep_evalue': min_rep_evalue, 'min_mob_evalue': min_mob_evalue, 'min_con_evalue': min_con_evalue,
               'min_rpp_evalue': min_rpp_evalue}

    for param in evalues:
        value = evalues[param]
        if value > 1:
            logging.error("Error: {} is too high, please specify an float evalue between 0 to 1".format(param))
            sys.exit(-1)

    min_overlapp = args.min_overlap

    min_length = args.min_length

    # Input Databases
    plasmid_ref_db = args.plasmid_db
    replicon_ref = args.plasmid_replicons
    mob_ref = args.plasmid_mob
    mash_db = args.plasmid_mash_db
    repetitive_mask_file = args.repetitive_mask

    check_dependencies(logging)

    needed_dbs = [plasmid_ref_db, replicon_ref, mob_ref, mash_db, repetitive_mask_file,"{}.nin".format(repetitive_mask_file)]

    for db in needed_dbs:
        if (not os.path.isfile(db)):
            logging.error('Error needed database missing "{}"'.format(db))
            sys.exit(-1)

    contig_report_file = os.path.join(out_dir, 'contig_report.txt')
    minimus_prefix = os.path.join(tmp_dir, 'minimus')
    filtered_blast = os.path.join(tmp_dir, 'filtered_blast.txt')
    repetitive_blast_report = os.path.join(out_dir, 'repetitive_blast_report.txt')
    mobtyper_results_file = os.path.join(out_dir, 'mobtyper_aggregate_report.txt')
    keep_tmp = args.keep_tmp

    run_circlator = args.run_circlator
    unicycler_contigs = args.unicycler_contigs

    if not isinstance(args.num_threads, int):
        logging.info('Error number of threads must be an integer, you specified "{}"'.format(args.num_threads))

    logging.info('Creating tmp working directory {}'.format(tmp_dir))

    if not os.path.isdir(tmp_dir):
        os.mkdir(tmp_dir, 0o755)

    logging.info('Writing cleaned header input fasta file from {} to {}'.format(input_fasta, fixed_fasta))
    fix_fasta_header(input_fasta, fixed_fasta)
    contig_seqs = read_fasta_dict(fixed_fasta)

    logging.info('Running replicon blast on {}'.format(replicon_ref))
    replicon_contigs = getRepliconContigs(
        replicon_blast(replicon_ref, fixed_fasta, min_rep_ident, min_rep_cov, min_rep_evalue, tmp_dir,
                       replicon_blast_results,
                       num_threads=num_threads))

    logging.info('Running relaxase blast on {}'.format(mob_ref))
    mob_contigs = getRepliconContigs(
        mob_blast(mob_ref, fixed_fasta, min_mob_ident, min_mob_cov, min_mob_evalue, tmp_dir, mob_blast_results,
                  num_threads=num_threads))

    logging.info('Running contig blast on {}'.format(plasmid_ref_db))
    contig_blast(fixed_fasta, plasmid_ref_db, min_con_ident, min_con_cov, min_con_evalue, min_length,
                 tmp_dir, contig_blast_results)

    pcl_clusters = contig_blast_group(filtered_blast, min_overlapp)

    logging.info('Running repetitive contig masking blast on {}'.format(mob_ref))
    repetitive_contigs = repetitive_blast(fixed_fasta, repetitive_mask_file, min_rpp_ident, min_rpp_cov, min_rpp_evalue,
                                          min_length, tmp_dir,
                                          repetitive_blast_results, num_threads=num_threads)

    circular_contigs = dict()

    logging.info('Running circlator minimus2 on {}'.format(fixed_fasta))
    if run_circlator:
        circular_contigs = circularize(fixed_fasta, minimus_prefix)

    if unicycler_contigs:
        for seqid in contig_seqs:
            if 'circular=true' in seqid:
                circular_contigs[seqid] = ''

    repetitive_dna = dict()
    results_fh = open(repetitive_blast_report, 'w')
    results_fh.write("contig_id\tmatch_id\tmatch_type\tscore\tcontig_match_start\tcontig_match_end\n")

    for contig_id in repetitive_contigs:
        match_info = repetitive_contigs[contig_id]['id'].split('|')
        repetitive_dna[contig_id] = "{}\t{}\t{}\t{}\t{}".format(
            match_info[1],
            match_info[len(match_info) - 1],
            repetitive_contigs[contig_id]['score'],
            repetitive_contigs[contig_id]['contig_start'],
            repetitive_contigs[contig_id]['contig_end'])
        results_fh.write("{}\t{}\t{}\t{}\t{}\t{}\n".format(contig_id,
                                                           match_info[1],
                                                           match_info[len(match_info) - 1],
                                                           repetitive_contigs[contig_id]['score'],
                                                           repetitive_contigs[contig_id]['contig_start'],
                                                           repetitive_contigs[contig_id]['contig_end']))

    results_fh.close()

    seq_clusters = dict()
    cluster_bitscores = dict()
    for seqid in pcl_clusters:
        cluster_id = list(pcl_clusters[seqid].keys())[0]
        bitscore = pcl_clusters[seqid][cluster_id]
        cluster_bitscores[cluster_id] = bitscore

    sorted_cluster_bitscores = sorted(list(cluster_bitscores.items()), key=operator.itemgetter(1))
    sorted_cluster_bitscores.reverse()
    contigs_assigned = dict()
    for cluster_id, bitscore in sorted_cluster_bitscores:

        if not cluster_id in seq_clusters:
            seq_clusters[cluster_id] = dict()
        for seqid in pcl_clusters:
            if not cluster_id in pcl_clusters[seqid]:
                continue
            if seqid in contig_seqs and seqid not in contigs_assigned:
                seq_clusters[cluster_id][seqid] = contig_seqs[seqid]
                contigs_assigned[seqid] = cluster_id

    # Add sequences with known replicons regardless of whether they belong to a mcl cluster
    clust_id = 0
    refined_clusters = dict()
    for contig_id in mob_contigs:
        if not contig_id in pcl_clusters:
            if contig_id in contig_seqs:
                if not clust_id in seq_clusters:
                    seq_clusters["Novel_" + str(clust_id)] = dict()
                    if not contig_id in pcl_clusters:
                        pcl_clusters[contig_id] = dict()

                    pcl_clusters[contig_id]["Novel_" + str(clust_id)] = 0
                seq_clusters["Novel_" + str(clust_id)][contig_id] = contig_seqs[contig_id]
            clust_id += 1

    # Add sequences with known relaxases regardless of whether they belong to a mcl cluster

    count_replicons = dict()
    for contig_id in replicon_contigs:
        if not contig_id in pcl_clusters:
            if contig_id in contig_seqs:
                if not clust_id in seq_clusters:
                    seq_clusters["Novel_" + str(clust_id)] = dict()
                    if not contig_id in pcl_clusters:
                        pcl_clusters[contig_id] = dict()

                    pcl_clusters[contig_id]["Novel_" + str(clust_id)] = dict()
                seq_clusters["Novel_" + str(clust_id)][contig_id] = contig_seqs[contig_id]
            clust_id += 1

    refined_clusters = dict()

    # split out circular sequences from each other

    replicon_clusters = dict()
    for contig_id in replicon_contigs:

        for hit_id in replicon_contigs[contig_id]:
            id, rep_type = hit_id.split('|')

            cluster = list(pcl_clusters[contig_id].keys())[0]
            if not cluster in replicon_clusters:
                replicon_clusters[cluster] = 0
            replicon_clusters[cluster] += 1

    for id in seq_clusters:
        cluster = seq_clusters[id]

        if not id in refined_clusters:
            refined_clusters[id] = dict()

        for contig_id in cluster:
            if contig_id in circular_contigs and len(cluster) > 1 and (
                    id in replicon_clusters and replicon_clusters[id] > 1):
                if not clust_id in refined_clusters:
                    refined_clusters["Novel_" + str(clust_id)] = dict()
                refined_clusters["Novel_" + str(clust_id)][contig_id] = cluster[contig_id]
                clust_id += 1
                continue

            refined_clusters[id][contig_id] = cluster[contig_id]

    seq_clusters = refined_clusters

    m = mash()
    mash_distances = dict()
    mash_top_dists = dict()
    contig_report = list()

    results_fh = open(contig_report_file, 'w')
    results_fh.write("file_id\tcluster_id\tcontig_id\tcontig_length\tcircularity_status\trep_type\t" \
                     "rep_type_accession\trelaxase_type\trelaxase_type_accession\tmash_nearest_neighbor\t"
                     " mash_neighbor_distance\trepetitive_dna_id\tmatch_type\tscore\tcontig_match_start\tcontig_match_end\n")

    filter_list = dict()
    counter = 0

    for cluster in seq_clusters:
        clusters = seq_clusters[cluster]
        total_cluster_length = 0

        count_seqs = len(clusters)
        count_rep = 0
        count_small = 0
        temp = dict()

        for contig_id in clusters:
            temp[contig_id] = ''
            if contig_id in repetitive_contigs:
                count_rep += 1
            length = len(clusters[contig_id])
            total_cluster_length += length
            if length < 3000:
                count_small += 1

        if count_rep == count_seqs or (float(
                count_rep) / count_seqs * 100 > 50 and count_small == count_seqs) or total_cluster_length < 1500:
            continue

        for contig_id in temp:
            filter_list[contig_id] = ''

        cluster_file = os.path.join(tmp_dir, 'clust_' + str(cluster) + '.fasta')
        mash_file = os.path.join(tmp_dir, 'clust_' + str(cluster) + '.txt')
        write_fasta_dict(clusters, cluster_file)

        mashfile_handle = open(mash_file, 'w')
        m.run_mash(mash_db, cluster_file, mashfile_handle)

        mash_results = m.read_mash(mash_file)
        mash_top_hit = getMashBestHit(mash_results)

        # delete low scoring clusters
        if float(mash_top_hit['mash_hit_score']) > 0.05:
            skip = True
            for contig_id in clusters:
                if contig_id in replicon_contigs:
                    skip = False
                    break
                if contig_id in circular_contigs:
                    skip = False
                    break
                if contig_id in mob_contigs:
                    skip = False
                    break
            if skip:
                for contig_id in clusters:
                    del (filter_list[contig_id])
                continue

        new_clust_file = None
        if os.path.isfile(cluster_file):
            if float(mash_top_hit['mash_hit_score']) < 0.05:
                cluster = mash_top_hit['clustid']
                new_clust_file = os.path.join(out_dir, 'plasmid_' + cluster + ".fasta")

            else:
                cluster = 'novel_' + str(counter)
                new_clust_file = os.path.join(out_dir, 'plasmid_' + cluster + ".fasta")
                counter += 1

            if os.path.isfile(new_clust_file):
                temp_fh = open(cluster_file, 'r')

                data = temp_fh.read()

                temp_fh.close()
                temp_fh = open(new_clust_file, 'a')
                temp_fh.write(data)
                temp_fh.close()
                mash_file = os.path.join(tmp_dir, 'clust_' + str(cluster) + '.txt')
                mashfile_handle = open(mash_file, 'w')
                m.run_mash(mash_db, cluster_file, mashfile_handle)
                mash_results = m.read_mash(mash_file)
                mash_top_hit = getMashBestHit(mash_results)

            else:
                os.rename(cluster_file, new_clust_file)

        if new_clust_file is not None:
            plasmid_files[new_clust_file] = ''

        for contig_id in clusters:
            found_replicon_string = ''
            found_replicon_id_string = ''
            found_mob_string = ''
            found_mob_id_string = ''
            contig_status = 'Incomplete'
            if contig_id in circular_contigs:
                contig_status = 'Circular'

            if contig_id in replicon_contigs:
                rep_ids = dict()
                rep_hit_ids = dict()

                for hit_id in replicon_contigs[contig_id]:
                    id, rep_type = hit_id.split('|')
                    rep_ids[rep_type] = ''
                    rep_hit_ids[id] = ''

                found_replicon_string = ','.join(list(rep_ids.keys()))
                found_replicon_id_string = ','.join(list(rep_hit_ids.keys()))

            if contig_id in mob_contigs:
                mob_ids = dict()
                mob_hit_ids = dict()

                for hit_id in mob_contigs[contig_id]:
                    id, mob_type = hit_id.split('|')
                    mob_ids[mob_type] = ''
                    mob_hit_ids[id] = ''

                found_mob_string = ','.join(list(mob_ids.keys()))
                found_mob_id_string = ','.join(list(mob_hit_ids.keys()))

            rep_dna_info = "\t\t\t\t"
            if contig_id in repetitive_dna:
                rep_dna_info = repetitive_dna[contig_id]

            results_fh.write("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(file_id, cluster, contig_id,
                                                                                       len(clusters[contig_id]),
                                                                                       contig_status,
                                                                                       found_replicon_string,
                                                                                       found_replicon_id_string,
                                                                                       found_mob_string,
                                                                                       found_mob_id_string,
                                                                                       mash_top_hit['top_hit'],
                                                                                       mash_top_hit['mash_hit_score'],
                                                                                       rep_dna_info))
    chr_contigs = dict()

    for contig_id in contig_seqs:
        if contig_id not in filter_list:
            chr_contigs[contig_id] = contig_seqs[contig_id]
            rep_dna_info = "\t\t\t\t"
            if contig_id in repetitive_dna:
                rep_dna_info = repetitive_dna[contig_id]
            contig_status = 'Incomplete'
            if contig_id in circular_contigs:
                contig_status = 'Circular'
            results_fh.write("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(file_id, 'chromosome', contig_id,
                                                                                       len(contig_seqs[contig_id]),
                                                                                       contig_status, '', '', '', '',
                                                                                       '', '', rep_dna_info))
    results_fh.close()
    write_fasta_dict(chr_contigs, chromosome_file)

    if args.run_typer:
        mobtyper_results = "file_id\tnum_contigs\ttotal_length\tgc\t" \
                           "rep_type(s)\trep_type_accession(s)\t" \
                           "relaxase_type(s)\trelaxase_type_accession(s)\t" \
                           "mpf_type\tmpf_type_accession(s)\t" \
                           "orit_type(s)\torit_accession(s)\tPredictedMobility\t" \
                           "mash_nearest_neighbor\tmash_neighbor_distance\tmash_neighbor_cluster\n"
        for file in plasmid_files:
            mobtyper_results = mobtyper_results + "{}".format(run_mob_typer(file, out_dir, str(num_threads)))
        fh = open(mobtyper_results_file, 'w')
        fh.write(mobtyper_results)
        fh.close()

    if not keep_tmp:
        shutil.rmtree(tmp_dir)


# call main function
if __name__ == '__main__':
    main()
