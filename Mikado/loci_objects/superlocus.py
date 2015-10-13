#!/usr/bin/env python3
# coding: utf-8

"""
Superlocus module. The class here defined is the uppermost container for transcripts
and is used to define all the possible children (subloci, monoloci, loci, etc.)
"""

# Core imports
import collections
import sqlalchemy
from sqlalchemy.engine import create_engine
from sqlalchemy.orm.session import sessionmaker
from sqlalchemy.sql.expression import and_
from sqlalchemy import bindparam
from sqlalchemy.ext import baked
import sqlalchemy.pool
from Mikado.serializers.junction import Junction, Chrom
from Mikado.loci_objects.abstractlocus import Abstractlocus
from Mikado.loci_objects.monosublocus import Monosublocus
from Mikado.loci_objects.excluded import Excluded
from Mikado.loci_objects.transcript import Transcript
from Mikado.loci_objects.sublocus import Sublocus
from Mikado.loci_objects.monosublocusholder import MonosublocusHolder
from Mikado.parsers.GFF import GffLine
import Mikado.exceptions


# The number of attributes is something I need
# pylint: disable=too-many-instance-attributes
class Superlocus(Abstractlocus):
    """The superlocus class is used to define overlapping regions
    on the genome, and it receives as input transcript class instances.
    """

    __name__ = "superlocus"
    available_metrics = Transcript.get_available_metrics()

    bakery = baked.bakery()
    db_baked = bakery(lambda session: session.query(Chrom))
    db_baked += lambda q: q.filter(Chrom.name == bindparam("chrom_name"))

    junction_baked = bakery(lambda session: session.query(Junction))
    junction_baked += lambda q: q.filter(and_(
        Junction.chrom == bindparam("chrom"),
        Junction.junction_start == bindparam("junctionStart"),
        Junction.junction_end == bindparam("junctionEnd")))
    # Junction.strand == bindparam("strand")))

    # ###### Special methods ############

    def __init__(self, transcript_instance, stranded=True, json_conf=None, logger=None):

        """

        :param transcript_instance: an instance of the Transcript class
        :type transcript_instance: Transcript
        :param stranded: boolean flag that indicates whether
        the Locus should use or ignore strand information
        :type stranded: bool
        :param json_conf: a configuration dictionary derived from JSON/YAML config files
        :type json_conf: dict
        :param logger: the logger for the class
        :type logger: logging.Logger

        The superlocus class is instantiated from a transcript_instance class,
        which it copies in its entirety.

        It will therefore have the following attributes:
        - chrom, strand, start, end
        - splices - a *set* which contains the position of each splice site
        - introns - a *set* which contains the positions of each
        *splice junction* (registered as 2-tuples)
        - transcripts - a *set* which holds the transcripts added to the superlocus

        The constructor method takes the following keyword arguments:
        - stranded    True if all transcripts inside the superlocus are required
        to be on the same strand
        - json_conf    Required. A dictionary with the coniguration necessary
        for scoring transcripts.
        - purge        Flag. If True, all loci holding only transcripts with a 0
        score will be deleted
        from further consideration.
        """

        super().__init__()
        self.stranded = stranded
        self.feature = self.__name__
        if json_conf is None or not isinstance(json_conf, dict):
            raise Mikado.exceptions.NoJsonConfigError(
                "I am missing the configuration for prioritizing transcripts!"
            )
        self.json_conf = json_conf
        self.purge = self.json_conf["run_options"]["purge"]

        self.splices = set(self.splices)
        self.introns = set(self.introns)
        super().add_transcript_to_locus(transcript_instance)
        assert transcript_instance.monoexonic is True or len(self.introns) > 0
        if self.stranded is True:
            self.strand = transcript_instance.strand
        self.logger = logger

        # Flags
        self.subloci_defined = False
        self.monosubloci_defined = False
        self.loci_defined = False
        self.monosubloci_metrics_calculated = False

        # Objects used during computation
        self.subloci = []
        self.locus_verified_introns = []
        self.loci = collections.OrderedDict()
        self.sublocus_metrics = []
        self.monosubloci = []
        self.monoholders = []

        # Connection objects
        self.engine = self.sessionmaker = self.session = None
        # Excluded object
        self.excluded_transcripts = None

    def __create_locus_lines(self, superlocus_line, new_id, print_cds=True):

        """
        Private method to prepare the lines for printing out loci
        into GFF/GTF files.
        """

        lines = []
        self.define_loci()
        if len(self.loci) > 0:
            source = "{0}_loci".format(self.source)
            superlocus_line.source = source
            lines.append(str(superlocus_line))
            found = dict()

            for _, locus_instance in self.loci.items():
                locus_instance.source = source
                locus_instance.parent = new_id
                if locus_instance.id in found:
                    found[locus_instance.id] += 1
                    locus_instance.counter = found[locus_instance.id]
                else:
                    found[locus_instance.id] = 0
                lines.append(locus_instance.__str__(print_cds=print_cds).rstrip())
        return lines

    def __create_monolocus_lines(self, superlocus_line, new_id, print_cds=True):

        """
        Private method to prepare the lines for printing out monosubloci
        into GFF/GTF files.
        """

        lines = []
        self.define_monosubloci()
        if len(self.monosubloci) > 0:
            source = "{0}_monosubloci".format(self.source)
            superlocus_line.source = source
            lines.append(str(superlocus_line))
            found = dict()
            for monosublocus_instance in self.monosubloci:
                monosublocus_instance.source = source
                monosublocus_instance.parent = new_id
                if monosublocus_instance.id in found:
                    found[monosublocus_instance.id] += 1
                    monosublocus_instance.counter = found[monosublocus_instance.id]
                else:
                    found[monosublocus_instance.id] = 0

                lines.append(monosublocus_instance.__str__(print_cds=print_cds).rstrip())

        return lines

    def __create_sublocus_lines(self, superlocus_line, new_id, print_cds=True):
        """
        Private method to prepare the lines for printing out subloci
        into GFF/GTF files.
        """

        source = "{0}_subloci".format(self.source)
        superlocus_line.source = source
        lines = [str(superlocus_line)]
        self.define_subloci()
        found = dict()
        for sublocus_instance in self.subloci:
            sublocus_instance.source = source
            sublocus_instance.parent = new_id
            if sublocus_instance.id in found:
                found[sublocus_instance.id] += 1
                sublocus_instance.counter = found[sublocus_instance.id]
            else:
                found[sublocus_instance.id] = 0
            lines.append(sublocus_instance.__str__(print_cds=print_cds).rstrip())
        return lines

    # This discrepancy with the base class is necessary
    # pylint: disable=arguments-differ
    def __str__(self, level=None, print_cds=True):

        """
        :param level: level which we wish to print for. Can be "loci", "subloci", "monosubloci"
        :type level: str
        :param print_cds: flag. If set to False, only the exonic information will be printed.
        :type print_cds: bool

        This function will return the desired level of children loci.
        The keyword level accepts the following four values:
        - "None" - print whatever is available.
        - "loci" - print the final loci
        - "monosubloci" - print the monosubloci
        - "subloci" - print the subloci.

        The function will then return the desired location in GFF3-compliant format.
        """

        if abs(self.start) == float("inf"):
            return ''

        superlocus_line = GffLine('')
        superlocus_line.chrom = self.chrom
        superlocus_line.feature = self.__name__
        superlocus_line.start, \
            superlocus_line.end, \
            superlocus_line.score = self.start, self.end, "."
        superlocus_line.strand = self.strand
        superlocus_line.phase, superlocus_line.score = None, None
        new_id = "{0}_{1}".format(self.source, self.id)
        superlocus_line.id, superlocus_line.name = new_id, self.name

        lines = []
        if level not in (None, "loci", "subloci", "monosubloci"):
            raise ValueError("Unrecognized level: {0}".format(level))

        elif level == "loci" or (level is None and self.loci_defined is True):
            lines = self.__create_locus_lines(
                superlocus_line,
                new_id,
                print_cds=print_cds
            )
        elif level == "monosubloci" or (level is None and self.monosubloci_defined is True):
            lines = self.__create_monolocus_lines(superlocus_line,
                                                  new_id,
                                                  print_cds=print_cds)
        elif level == "subloci" or (level is None and self.monosubloci_defined is False):
            lines = self.__create_sublocus_lines(superlocus_line,
                                                 new_id,
                                                 print_cds=print_cds)
        if len(lines) > 0:
            lines.append("###")
        return "\n".join([line for line in lines if line is not None and line != ''])
    # pylint: enable=arguments-differ

    # ########### Class instance methods ############

    def split_strands(self):
        """This method will divide the superlocus on the basis of the strand.
        The rationale is to parse a GFF file without regard for the
        strand, in order to find all intersecting loci;
        and subsequently break the superlocus into the different components.
        Notice that each strand might generate more than one superlocus,
        if genes on a different strand link what are
        two different superloci.
        """

        self.logger.debug("Splitting by strand for {0}".format(self.id))
        if self.stranded is True:
            self.logger.warning("Trying to split by strand a stranded Locus, {0}!".format(self.id))
            yield self

        else:
            plus, minus, nones = [], [], []
            for cdna_id in self.transcripts:
                cdna = self.transcripts[cdna_id]
                self.logger.debug("{0}: strand {1}".format(cdna_id, cdna.strand))
                if cdna.strand == "+":
                    plus.append(cdna)
                elif cdna.strand == "-":
                    minus.append(cdna)
                elif cdna.strand is None:
                    nones.append(cdna)

            new_loci = []
            for strand in plus, minus, nones:
                if len(strand) > 0:
                    strand = sorted(strand)
                    new_locus = Superlocus(strand[0],
                                           stranded=True,
                                           json_conf=self.json_conf,
                                           logger=self.logger)
                    assert len(new_locus.introns) > 0 or new_locus.monoexonic is True
                    for cdna in strand[1:]:
                        if new_locus.in_locus(new_locus, cdna):
                            new_locus.add_transcript_to_locus(cdna)
                        else:
                            assert len(new_locus.introns) > 0 or new_locus.monoexonic is True
                            new_loci.append(new_locus)
                            new_locus = Superlocus(cdna,
                                                   stranded=True,
                                                   json_conf=self.json_conf,
                                                   logger=self.logger)
                    assert len(new_locus.introns) > 0 or new_locus.monoexonic is True
                    new_loci.append(new_locus)

            self.logger.debug(
                "Defined %d loci by splitting by strand at %s.",
                len(new_loci), self.id)
            for new_locus in iter(sorted(new_loci)):
                yield new_locus
        raise StopIteration

    # @profile
    def connect_to_db(self, pool):

        """
        :param pool: the connection pool
        :type pool: sqlalchemy.pool.QueuePool

        This method will connect to the database using the information
        contained in the JSON configuration.
        """

        database = self.json_conf["db_settings"]["db"]
        dbtype = self.json_conf["db_settings"]["dbtype"]

        if pool is None:
            self.engine = create_engine("{dbtype}:///{db}".format(
                db=database,
                dbtype=dbtype), poolclass=sqlalchemy.pool.StaticPool)
        else:
            self.engine = create_engine("{dbtype}://".format(dbtype=dbtype),
                                        pool=pool)

        self.sessionmaker = sessionmaker()
        self.sessionmaker.configure(bind=self.engine)
        self.session = self.sessionmaker()

    # @asyncio.coroutine
    def load_transcript_data(self, tid, data_dict):
        """
        :param tid: the name of the transcript to retrieve data for.
        :type tid: str

        This routine is used to load data for a single transcript."""

        self.logger.debug("Retrieving data for {0}".format(tid))
        self.transcripts[tid].logger = self.logger
        self.transcripts[tid].load_information_from_db(self.json_conf,
                                                       introns=self.locus_verified_introns,
                                                       session=self.session,
                                                       data_dict=data_dict)
        to_remove, to_add = False, set()

        if self.json_conf["chimera_split"]["execute"] is True:
            if self.transcripts[tid].number_internal_orfs > 1:
                new_tr = list(self.transcripts[tid].split_by_cds())
                if len(new_tr) > 1:
                    to_add.update(new_tr)
                    to_remove = True
        return to_remove, to_add
        # @profile

    def _load_introns(self, data_dict):

        """Private method to load the intron data into the locus.
        :param data_dict: Dictionary containing the preloaded data, if available.
        :param pool: the SQL connection pool, if available.
        :return:
        """

        self.locus_verified_introns = []
        if len(self.introns) == 0:
            if self.monoexonic is False:
                raise ValueError("%s is multiexonic but has no introns defined!",
                                 self.id)
            self.logger.debug("No introns for %s", self.id)
            return

        self.logger.debug("Querying the DB for introns, %d total", len(self.introns))
        if data_dict is None:
            if self.json_conf["db_settings"]["db"] is None:
                return  # No data to load
            # dbquery = self.db_baked(self.session).params(chrom_name=self.chrom).all()
            for intron in self.introns:
                self.logger.debug("Checking %s%s:%d-%d",
                                  self.chrom, self.strand, intron[0], intron[1])
                if len(self.junction_baked(self.session).params(
                                chrom=self.chrom,
                                junctionStart=intron[0],
                                junctionEnd=intron[1],
                                junctionStrand=self.strand
                        ).all()) == 1:
                    self.logger.debug("Verified intron %s:%d-%d",
                                      self.chrom, intron[0], intron[1])
                    self.locus_verified_introns.append(intron)
        else:
            for intron in self.introns:
                self.logger.debug("Checking %s%s:%d-%d",
                                  self.chrom, self.strand, intron[0], intron[1])
                if (self.chrom, intron[0], intron[1], self.strand) in data_dict["junctions"]:
                    self.logger.debug("Verified intron %s%s:%d-%d",
                                      self.chrom, self.strand, intron[0], intron[1])
                    self.locus_verified_introns.append(intron)

    def load_all_transcript_data(self, pool=None, data_dict=None):

        """
        This method will load data into the transcripts instances,
        and perform the split_by_cds if required
        by the configuration.
        Asyncio coroutines are used to decrease runtime.

        :param pool: a connection pool
        :type pool: sqlalchemy.pool.QueuePool
        """

        if data_dict is None:
            self.connect_to_db(pool)
        self.logger.debug("Type of data dict: %s",
                          type(data_dict))
        if isinstance(data_dict, dict):
            self.logger.debug("Length of data dict: %s", len(data_dict))
        self._load_introns(data_dict)
        tid_keys = self.transcripts.keys()
        to_remove, to_add = set(), set()
        for tid in tid_keys:
            remove_flag, new_transcripts = self.load_transcript_data(tid, data_dict)
            if remove_flag is True:
                to_remove.add(tid)
                to_add.update(new_transcripts)

        if len(to_remove) > 0:
            self.logger.debug("Adding to %s: %s",
                              self.id,
                              ",".join([tr.id for tr in to_add]))
            for transcr in to_add:
                self.add_transcript_to_locus(transcr, check_in_locus=False)
            self.logger.debug("Removing from %s: %s",
                              self.id,
                              ",".join(list(to_remove)))
            for tid in to_remove:
                self.remove_transcript_from_locus(tid)

        if data_dict is None:
            self.session.close()
            self.sessionmaker.close_all()

        num_coding = 0
        for tid in self.transcripts:
            if self.transcripts[tid].combined_cds_length > 0:
                num_coding += 1
            else:
                self.transcripts[tid].feature = "ncRNA"

        # num_coding = sum(1 for x in self.transcripts
        #                  if self.transcripts[x].selected_cds_length > 0)
        self.logger.debug(
            "Found %d coding transcripts out of %d in %s",
            num_coding,
            len(self.transcripts),
            self.id)

        self.session = None
        self.sessionmaker = None
        self.stranded = False

    # ##### Sublocus-related steps ######

    def __prefilter_transcripts(self):

        """Private method that will check whether there are any transcripts
        not meeting the minimum requirements specified in the configuration.
        :return:
        """

        not_passing = set()
        self.excluded_transcripts = None

        if "requirements" in self.json_conf:
            for tid in self.transcripts:
                evaluated = dict()
                for key in self.json_conf["requirements"]["parameters"]:
                    name = self.json_conf["requirements"]["parameters"][key]["name"]
                    value = getattr(self.transcripts[tid], name)
                    evaluated[key] = self.evaluate(
                        value,
                        self.json_conf["requirements"]["parameters"][key])

                # This is by design
                # pylint: disable=eval-used
                if eval(self.json_conf["requirements"]["compiled"]) is False:
                    self.logger.debug("Discarding %s", tid)
                    not_passing.add(tid)
                    self.transcripts[tid].score = 0
                # pylint: enable=eval-used
        else:
            return

        if len(not_passing) > 0 and self.purge is True:
            tid = not_passing.pop()
            self.transcripts[tid].score = 0
            monosub = Monosublocus(self.transcripts[tid], logger=self.logger)
            self.excluded_transcripts = Excluded(monosub,
                                                 json_conf=self.json_conf,
                                                 logger=self.logger)
            self.excluded_transcripts.__name__ = "Excluded"
            self.remove_transcript_from_locus(tid)
            for tid in not_passing:
                self.transcripts[tid].score = 0
                self.excluded_transcripts.add_transcript_to_locus(
                    self.transcripts[tid])
                self.remove_transcript_from_locus(tid)
        return

    def define_subloci(self):
        """This method will define all subloci inside the superlocus.
        Steps:
            - Call the BronKerbosch algorithm to define cliques
            - Call the "merge_cliques" algorithm the merge the cliques.
            - Create "sublocus" objects from the merged cliques
            and store them inside the instance store "subloci"
        """

        self.compile_requirements()
        if self.subloci_defined is True:
            return
        self.subloci = []

        # Check whether there is something to remove
        self.__prefilter_transcripts()

        if len(self.transcripts) == 0:
            # we have removed all transcripts from the Locus. Set the flag to True and exit.
            self.subloci_defined = True
            return

        cds_only = self.json_conf["run_options"]["subloci_from_cds_only"]
        transcript_graph = self.define_graph(self.transcripts,
                                             inters=self.is_intersecting,
                                             cds_only=cds_only)
        _, subloci = self.find_communities(transcript_graph)

        # Now we should define each sublocus and store it in a permanent structure of the class
        for subl in subloci:
            if len(subl) == 0:
                continue
            subl = [self.transcripts[x] for x in subl]
            subl = sorted(subl)
            new_sublocus = Sublocus(subl[0], json_conf=self.json_conf, logger=self.logger)
            for ttt in subl[1:]:
                try:
                    new_sublocus.add_transcript_to_locus(ttt)
                except Mikado.exceptions.NotInLocusError as orig_exc:
                    exc_text = """Sublocus: {0}
                    Offending transcript:{1}
                    In locus manual check: {2}
                    Original exception: {3}""".format(
                        "{0} {1}:{2}-{3} {4}".format(
                            subl[0].id, subl[0].chrom, subl[0].start,
                            subl[0].end, subl[0].exons),
                        "{0} {1}:{2}-{3} {4}".format(ttt.id, ttt.chrom,
                                                     ttt.start, ttt.end, ttt.exons),
                        "Chrom {0} Strand {1} overlap {2}".format(
                            new_sublocus.chrom == ttt.chrom,
                            "{0}/{1}/{2}".format(
                                new_sublocus.strand,
                                ttt.strand,
                                new_sublocus.strand == ttt.strand
                            ),
                            self.overlap((subl[0].start, subl[1].end),
                                         (ttt.start, ttt.end)) > 0
                        ),
                        orig_exc
                    )
                    raise Mikado.exceptions.NotInLocusError(exc_text)

            new_sublocus.parent = self.id
            self.subloci.append(new_sublocus)
        self.subloci = sorted(self.subloci)

        self.subloci_defined = True

    def get_sublocus_metrics(self):
        """Wrapper function to calculate the metrics inside each sublocus."""

        self.define_subloci()
        self.sublocus_metrics = []
        for sublocus_instance in self.subloci:
            sublocus_instance.get_metrics()

    def define_monosubloci(self):

        """This is a wrapper method that defines the monosubloci for each sublocus.
        """
        if self.monosubloci_defined is True:
            return

        self.define_subloci()
        self.monosubloci = []
        # Extract the relevant transcripts
        for sublocus_instance in sorted(self.subloci):
            self.excluded_transcripts = sublocus_instance.define_monosubloci(
                purge=self.purge,
                excluded=self.excluded_transcripts)
            for tid in sublocus_instance.transcripts:
                # Update the score
                self.transcripts[tid].score = sublocus_instance.transcripts[tid].score
            for monosubl in sublocus_instance.monosubloci:
                monosubl.parent = self.id
                self.monosubloci.append(monosubl)
        self.monosubloci = sorted(self.monosubloci)
        self.monosubloci_defined = True

    def print_subloci_metrics(self):
        """Wrapper method to create a csv.DictWriter instance and call
        the sublocus.print_metrics method
        on it for each sublocus."""

        self.get_sublocus_metrics()

        for slocus in self.subloci:
            for row in slocus.print_metrics():
                yield row
        if self.excluded_transcripts is not None:
            for row in self.excluded_transcripts.print_metrics():
                yield row

    def print_subloci_scores(self):
        """Wrapper method to create a csv.DictWriter instance and call the
        sublocus.print_metrics method
        on it for each sublocus."""

        self.get_sublocus_metrics()

        for slocus in self.subloci:
            for row in slocus.print_scores():
                yield row
        # if self.excluded_transcripts is not None:
        #     for row in self.excluded_transcripts.print_scores():
        #         yield row

    def print_monoholder_metrics(self):

        """Wrapper method to create a csv.DictWriter instance and call the
        MonosublocusHolder.print_metrics method
        on it."""

        self.define_loci()

        # self.available_monolocus_metrics = set(self.monoholder.available_metrics)
        if len(self.monoholders) == 0:
            return
        for monoholder in self.monoholders:
            for row in monoholder.print_metrics():
                yield row

    def print_monoholder_scores(self):

        """Wrapper method to create a csv.DictWriter instance and call
        the MonosublocusHolder.print_scores method on it."""

        self.define_loci()

        # self.available_monolocus_metrics = set(self.monoholder.available_metrics)
        if len(self.monoholders) == 0:
            return
        for monoholder in self.monoholders:
            for row in monoholder.print_scores():
                yield row

    def define_loci(self):
        """This is the final method in the pipeline. It creates a container
        for all the monosubloci (an instance of the class MonosublocusHolder)
        and retrieves the loci it calculates internally."""

        if self.loci_defined is True:
            return

        self.define_monosubloci()
        self.calculate_mono_metrics()

        self.loci = collections.OrderedDict()
        if len(self.monoholders) == 0:
            self.loci_defined = True
            return

        loci = []
        for monoholder in self.monoholders:
            monoholder.define_loci(purge=self.purge)
            for locus_instance in monoholder.loci:
                monoholder.loci[locus_instance].parent = self.id
                loci.append(monoholder.loci[locus_instance])

        for locus in sorted(loci):
            self.loci[locus.id] = locus

        self.loci_defined = True
        if self.json_conf["alternative_splicing"]["report"] is True:
            self.define_alternative_splicing()

        return

    def define_alternative_splicing(self):

        """
         This method will consider all possible candidates for alternative splicing
         for each of the final loci, after excluding transcripts which potentially map
         to more than one Locus (in order to remove chimeras).
         It will then call the add_transcript_to_locus method to try to add
         the transcript to the relevant Locus container.
        """

        # First off, define genes

        self.define_loci()

        candidates = collections.defaultdict(set)
        primary_transcripts = set(locus.primary_transcript_id for locus in self.loci.values())

        cds_only = self.json_conf["run_options"]["subloci_from_cds_only"]
        t_graph = self.define_graph(self.transcripts,
                                    inters=MonosublocusHolder.is_intersecting,
                                    cds_only=cds_only)
        cliques, _ = self.find_communities(t_graph)

        loci_cliques = dict()

        for lid, locus_instance in self.loci.items():
            self.loci[lid].logger = self.logger
            self.loci[lid].set_json_conf(self.json_conf)
            loci_cliques[lid] = set()
            for clique in cliques:
                if locus_instance.primary_transcript_id in clique:
                    loci_cliques[
                        locus_instance.id].update({tid for tid in clique if
                                                   tid != locus_instance.primary_transcript_id})

        for tid in iter(tid for tid in self.transcripts if tid not in primary_transcripts):
            loci_in = list(llid for llid in loci_cliques if
                           tid in loci_cliques[llid])
            if len(loci_in) == 1:
                candidates[loci_in[0]].add(tid)

        for lid in candidates:
            for tid in sorted(candidates[lid],
                              key=lambda ttid: self.transcripts[ttid].score,
                              reverse=True):
                self.loci[lid].add_transcript_to_locus(self.transcripts[tid])

    def calculate_mono_metrics(self):
        """Wrapper to calculate the metrics for the monosubloci."""
        self.monoholders = []

        for monosublocus_instance in sorted(self.monosubloci):
            found_holder = False
            for holder in self.monoholders:
                if MonosublocusHolder.in_locus(holder, monosublocus_instance):
                    holder.add_monosublocus(monosublocus_instance)
                    found_holder = True
                    break
            if found_holder is False:
                holder = MonosublocusHolder(
                    monosublocus_instance,
                    json_conf=self.json_conf,
                    logger=self.logger)
                self.monoholders.append(holder)

        for monoholder in self.monoholders:
            monoholder.calculate_scores()

    def compile_requirements(self):
        """Quick function to evaluate the filtering expression, if it is present."""

        if "requirements" in self.json_conf:
            if "compiled" in self.json_conf["requirements"]:
                return
            else:
                self.json_conf["requirements"]["compiled"] = compile(
                    self.json_conf["requirements"]["expression"],
                    "<json>", "eval")
                return
        else:
            return

    # ############ Class methods ###########

    # The discrepancy is by design
    # pylint: disable=arguments-differ
    @classmethod
    def is_intersecting(cls, transcript, other, cds_only=False):
        """
        :rtype : bool
        :param transcript: a transcript for which we wish to verify
        whether it is intersecting with another transcript or not.
        :type transcript: Mikado.loci_objects.transcript.Transcript
        :param other: the transcript which will be used for the comparison.
        :type other: Mikado.loci_objects.transcript.Transcript

        :param cds_only: boolean flag. If enabled, only CDS exons/intron
        will be considered when deciding whether two transcripts are part
        of the same Locus or not.
        :type cds_only: bool


        When comparing two transcripts, for the definition of subloci inside
        superloci we follow these rules:

        If both are multiexonic, the function verifies whether there is at
        least one intron in common.
        If both are monoexonic, the function verifies whether there is some overlap between them.
        If one is monoexonic and the other is not, the function will return False by definition.
        """

        transcript.finalize()
        other.finalize()
        if transcript.id == other.id:
            return False  # We do not want intersection with oneself

        if transcript.monoexonic is False and other.monoexonic is False:
            if cds_only is False:
                intersection = set.intersection(transcript.introns, other.introns)
            else:
                intersection = set.intersection(transcript.combined_cds_introns,
                                                other.combined_cds_introns)
            if len(intersection) > 0:
                intersecting = True
            else:
                intersecting = False

        elif transcript.monoexonic is True and other.monoexonic is True:
            if transcript.start == other.start or transcript.end == other.end:
                intersecting = True
            else:
                test_result = cls.overlap(
                    (transcript.start, transcript.end),
                    (other.start, other.end)
                )
                intersecting = test_result > 0
        else:
            intersecting = False

        return intersecting
    # pylint: enable=arguments-differ

    # ############## Properties ############
    @property
    def id(self) -> str:
        """
        This is a generic string generator for all inherited children.
        :rtype : str
        """
        if self.stranded is True:
            strand = self.strand
        else:
            strand = "mixed"
        return "{0}:{1}{2}:{3}-{4}".format(
            self.__name__,
            self.chrom,
            strand,
            self.start,
            self.end)