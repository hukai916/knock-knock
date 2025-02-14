import contextlib
import shlex
import subprocess
import tempfile
from pathlib import Path

import pysam

from hits import sam, fasta, fastq

HARD_CLIP = sam.BAM_CHARD_CLIP
SOFT_CLIP = sam.BAM_CSOFT_CLIP

def blast(ref_fn,
          reads,
          bam_fn=None,
          bam_by_name_fn=None,
          max_insertion_length=None,
          manual_temp_dir=None,
          return_alignments=False,
         ):
    ''' ref_fn: either a path to a fasta file, or a dictionary of reference sequences
        reads: either a path to a fastq/fastq.gz file, or a list of such paths, or an iterator over hits.fastq.Read objects
        bam_fn: path to write reference coordinate-sorted alignments
        bam_by_name_fn: path to write query name-sorted alignments
        max_insertion_length: If not None, any alignments with insertions longer than max_insertion_length will be split into multiple alignments.
    '''
    with tempfile.TemporaryDirectory(suffix='_blast', dir=manual_temp_dir) as temp_dir:
        temp_dir_path = Path(temp_dir)

        if isinstance(ref_fn, dict):
            # Make a temporary ref file.
            temp_ref_fn = temp_dir_path / 'refs.fasta'
            fasta.write_dict(ref_fn, temp_ref_fn)
            ref_fn = temp_ref_fn

        reads_fasta_fn = temp_dir_path / 'reads.fasta'

        sam_fn = temp_dir_path / 'alignments.sam'

        fastq_dict = {
            '+': {},
            '-': {},
        }

        if isinstance(reads, list) and isinstance(reads[0], fastq.Read):
            # Need to exempt lists of Reads from being passed to fastq.reads below
            pass
        elif isinstance(reads, (str, Path, list)):
            reads = fastq.reads(reads, up_to_space=True)

        with reads_fasta_fn.open('w') as fasta_fh:
            for read in reads:
                fastq_dict['+'][read.name] = read
                fastq_dict['-'][read.name] = read.reverse_complement()

                if len(read) > 0:
                    fasta_read = fasta.Read(read.name, read.seq)
                    fasta_fh.write(str(fasta_read))

        pysam.faidx(str(reads_fasta_fn))
            
        blast_command = [
            'blastn',
            '-task', 'blastn', # default is megablast
            '-evalue', '0.1',
            '-gapopen', '10',
            '-gapextend', '4',
            '-max_target_seqs', '1000000',
            '-parse_deflines', # otherwise qnames/rnames are lost
            '-outfmt', '17', # SAM output
            '-subject', str(reads_fasta_fn), # for bowtie-like behavior, reads are subject ...
            '-query', str(ref_fn), # ... and refs are query
            '-out', str(sam_fn),
        ]

        try:
            subprocess.run(blast_command,
                           check=True,
                           stderr=subprocess.PIPE,
                           stdout=subprocess.PIPE,
                          )
        except subprocess.CalledProcessError as e:
            print(f'blastn command returned code {e.returncode}')
            print(f'full command was:\n\n{shlex.join(blast_command)}\n')
            print(f'stdout from blastn was:\n\n{e.stdout.decode()}\n')
            print(f'stderr from blastn was:\n\n{e.stderr.decode()}\n')
            raise

        def undo_hard_clipping(al):
            strand = sam.get_strand(al)
            read = fastq_dict[strand][al.query_name]

            al.query_sequence = read.seq
            al.query_qualities = read.query_qualities

            al.cigar = [(SOFT_CLIP if k == HARD_CLIP else k, l) for k, l in al.cigar]
    
        def make_unaligned(read):
            unal = pysam.AlignedSegment()
            unal.query_name = read.name
            unal.is_unmapped = True
            unal.query_sequence = read.seq
            unal.query_qualities = read.query_qualities
            return unal

        try:
            sam_fh = pysam.AlignmentFile(str(sam_fn))
            header = sam_fh.header
        except ValueError:
            # blast had no output
            header = sam.header_from_fasta(ref_fn)
            pysam.AlignmentFile(str(sam_fn), 'wb', header=header).close()
            sam_fh = pysam.AlignmentFile(str(sam_fn))

        if bam_fn is not None:
            sorter = sam.AlignmentSorter(bam_fn, header)
        else:
            sorter = contextlib.nullcontext()

        if bam_by_name_fn is not None:
            by_name_sorter = sam.AlignmentSorter(bam_by_name_fn, header, by_name=True)
        else:
            by_name_sorter = contextlib.nullcontext()

        alignments = []

        with sorter, by_name_sorter:
            aligned_names = set()
            for al in sam_fh:
                aligned_names.add(al.query_name)

                undo_hard_clipping(al)

                if max_insertion_length is not None:
                    split_als = sam.split_at_large_insertions(al, max_insertion_length + 1)
                else:
                    split_als = [al]

                for split_al in split_als:
                    if bam_fn is not None:
                        sorter.write(split_al)
                    if bam_by_name_fn is not None:
                        by_name_sorter.write(split_al)

                    if return_alignments:
                        alignments.append(split_al)

            for name in fastq_dict['+']:
                if name not in aligned_names:
                    unal = make_unaligned(fastq_dict['+'][name])
                    if bam_by_name_fn is not None:
                        by_name_sorter.write(unal)
                    if return_alignments:
                        alignments.append(unal)

        return alignments
