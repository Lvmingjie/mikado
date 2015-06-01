import operator
import os.path,sys
from sqlalchemy.engine import create_engine
from sqlalchemy.orm.session import sessionmaker
from loci_objects.junction import junction, Chrom
from loci_objects.orf import orf
from sqlalchemy.sql.expression import desc, asc
from loci_objects.blast_utils import Query, Hit
from _collections import defaultdict
import itertools
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import inspect
from loci_objects.abstractlocus import abstractlocus # Needed for the BronKerbosch algorithm ...
from loci_objects.GTF import gtfLine
from loci_objects.GFF import gffLine
import loci_objects.exceptions
from sqlalchemy import and_
#import logging

class metric(property):
    '''Simple aliasing of property. All transcript metrics should use this alias, not "property", as a decorator.'''
    pass

class transcript:
    
    __name__ = "transcript"
    
    '''This class defines a transcript, down to its exon/CDS/UTR components. It is instantiated by a transcript
    GT/GFF3 line.
    Key attributes:
    - chrom    The chromosome
    - source
    - feature            mRNA if at least one CDS is defined
    - start
    - end
    - score
    - strand            one of +,-,None
    - phase            set to None
    - id            the ID of the transcripts (or tid)
    - parent        
    - attributes    a dictionary with additional informations from the GFFline
    
    After all exons have been loaded into the instance (see "addExon"), the class must be finalized with the appropriate method.

    CDS locations can be uploaded from the external, using a dictionary of indexed BED12 entries. 
    '''
    
    ######### Class special methods ####################
    
    def __init__(self, *args, source=None):
        
        '''Initialise the transcript object, using a mRNA/transcript line.
        Note: I am assuming that the input line is an object from my own "GFF" class.
        The transcript instance must be initialised by a "(m|r|lnc|whatever)RNA" or "transcript" gffLine.'''
        
        self.chrom = None
        self.exons, self.combined_cds, self.combined_utr = [], [], []
        self.introns = []
        self.splices = []
        self.finalized = False # Flag. We do not want to repeat the finalising more than once.        
        self.selected_internal_orf_index = None
        self.has_start_codon, self.has_stop_codon = False,False
        self.non_overlapping_cds = None
        
        if len(args)==0:
            return
        else:
            transcript_row=args[0]
            if type(transcript_row) not in (gffLine, gtfLine):
                raise TypeError("Invalid data type: {0}".format(type(transcript_row))) 
                    
        self.chrom = transcript_row.chrom
        assert "transcript"==transcript_row.feature or "RNA" in transcript_row.feature.upper()
        self.feature="transcript"
        self.id = transcript_row.id
        self.name = transcript_row.name
        if source is None:
            self.source=transcript_row.source
        else:
            self.source=source
        self.start=transcript_row.start
        self.strand = transcript_row.strand
        self.end=transcript_row.end
        self.score = transcript_row.score

        self.parent = transcript_row.parent
        self.attributes = transcript_row.attributes
        
    def __str__(self, to_gtf=False, print_cds=True):
        '''Each transcript will be printed out in the GFF style.
        This is pretty rudimentary, as the class does not hold any information on the original source, feature, score, etc.'''
        
        self.finalize() #Necessary to sort the exons
        lines = []
        transcript_counter = 0
#         assert self.selected_internal_orf_index > -1

        if self.strand is None:
            strand="."
        else:   
            strand=self.strand
        
        if to_gtf is True:
            parent_line = gtfLine('')
        else:
            parent_line=gffLine('')

        if print_cds is True:
            
            for index in range(len(self.internal_orfs)):
                
                if self.number_internal_orfs>1:
                    transcript_counter+=1
                    tid = "{0}.orf{1}".format(self.id, transcript_counter)
                    
                    if index==self.selected_internal_orf_index: self.attributes["maximal"]=True
                    else: self.attributes["maximal"]=False
                else:
                    tid = self.id
                cds_run = self.internal_orfs[index]
                    
                parent_line.chrom=self.chrom
                parent_line.source=self.source
                parent_line.feature=self.feature
                parent_line.start,parent_line.end=self.start,self.end
                parent_line.score=self.score
                parent_line.strand=strand
                parent_line.phase='.'
                parent_line.attributes=self.attributes
                
                parent_line.parent=self.parent
                parent_line.id=tid
                parent_line.name = self.id
            
                exon_lines = []
            
                cds_begin = False
            
                cds_count=0
                exon_count=0
                five_utr_count=0
                three_utr_count=0
    
                for segment in cds_run:
                    if cds_begin is False and segment[0]=="CDS": cds_begin = True
                    if segment[0]=="UTR":
                        if cds_begin is True:
                            if to_gtf is True:
                                if self.strand=="-": feature="5UTR"
                                else: feature="3UTR"
                            else:
                                if self.strand=="-": feature="five_prime_UTR"
                                else: feature="three_prime_UTR"
                        else:
                            if to_gtf is True:
                                if self.strand=="-": feature="3UTR"
                                else: feature="5UTR"
                            else:
                                if self.strand=="-": feature="three_prime_UTR"
                                else: feature="five_prime_UTR"
                        if "five" in feature or "5" in feature:
                            five_utr_count+=1
                            index=five_utr_count
                        else:
                            three_utr_count+=1
                            index=three_utr_count
                    else:
                        if segment[0]=="CDS":
                            cds_count+=1
                            index=cds_count
                        else:
                            exon_count+=1
                            index=exon_count
                        feature=segment[0]
                    if to_gtf is True:
                        exon_line=gtfLine('')
                    else:
                        exon_line=gffLine('')
                        
                    exon_line.chrom=self.chrom
                    exon_line.source=self.source
                    exon_line.feature=feature
                    exon_line.start,exon_line.end=segment[1],segment[2]
                    exon_line.strand=strand
                    exon_line.phase=None
                    exon_line.score = None
                    if to_gtf is True:
                        exon_line.gene=self.parent
                        exon_line.transcript=tid
                    else:
                        exon_line.id="{0}.{1}{2}".format(tid, feature,index)
                        exon_line.parent=tid
                        
                    exon_lines.append(str(exon_line))
            
            
                lines.append(str(parent_line))
                lines.extend(exon_lines) 
        else:
            if to_gtf is True:
                parent_line = gtfLine('')
            else:
                parent_line=gffLine('')
                    
            parent_line.chrom=self.chrom
            parent_line.source=self.source
            parent_line.feature=self.feature
            parent_line.start,parent_line.end=self.start,self.end
            parent_line.score=self.score
            parent_line.strand=strand
            parent_line.phase='.'
            parent_line.attributes=self.attributes
                
            parent_line.parent=self.parent
            parent_line.id=self.id
            parent_line.name = self.id
            
            lines=[str(parent_line)]
            exon_lines = []
            
            exon_count=0
            for exon in self.exons:
                exon_count+=1
                if to_gtf is True:
                    exon_line = gtfLine('')
                else:
                    exon_line = gffLine('')
                exon_line.chrom=self.chrom
                exon_line.source=self.source
                exon_line.feature="exon"
                exon_line.start,exon_line.end=exon[0],exon[1]
                exon_line.score=None
                exon_line.strand=strand
                exon_line.phase=None
                exon_line.attributes=self.attributes
                
                exon_line.id="{0}.{1}{2}".format(self.id, "exon",exon_count)
                exon_line.parent=self.id
                exon_lines.append(str(exon_line))
            
            lines.extend(exon_lines)
        
        return "\n".join(lines)
    
    def __eq__(self, other):
        '''Two transcripts are considered identical if they have the same
        start, end, chromosome, strand and internal exons.
        IDs are not important for this comparison; two transcripts coming from different
        methods and having different IDs can still be identical.'''
        
        if not type(self)==type(other): return False
        self.finalize()
        other.finalize()
           
        if self.strand == other.strand and self.chrom == other.chrom and \
            self.start==other.start and self.end == other.end and \
            self.exons == other.exons:
            return True
          
        return False
    
    def __hash__(self):
        '''Returns the hash of the object (call to super().__hash__()).
        Necessary to be able to add these objects to hashes like sets.'''

        return super().__hash__()
    
    def __len__(self):
        '''Returns the length occupied by the unspliced transcript on the genome.'''
        return self.end-self.start+1

     
    def __lt__(self, other):
        '''A transcript is lesser than another if it is on a lexicographic inferior chromosome,
        or if it begins before the other, or (in the case where they begin at the same location)
        it ends earlier than the other.'''
        if self.chrom!=other.chrom:
            return self.chrom<other.chrom
        if self==other:
            return False
        if self.start<other.start:
            return True
        elif self.start==other.start and self.end<other.end:
            return True
        return False
     
    def __gt__(self, other):
        return not self<other
     
    def __le__(self, other):
        return (self==other) or (self<other)
     
    def __ge__(self, other):
        return (self==other) or (self>other) 
        
    ######### Class instance methods ####################


    def addExon(self, gffLine):
        '''This function will append an exon/CDS feature to the object.'''

        if self.finalized is True:
            raise loci_objects.exceptions.ModificationError("You cannot add exons to a finalized transcript!")
        
        if self.id not in gffLine.parent:
            raise loci_objects.exceptions.InvalidTranscript("""Mismatch between transcript and exon:\n
            {0}\n
            {1}\n
            {2}
            """.format(self.id, gffLine.parent,gffLine))
        if gffLine.feature=="CDS":
            store=self.combined_cds
        elif "combined_utr" in gffLine.feature or "UTR" in gffLine.feature:
            store=self.combined_utr
        elif gffLine.feature=="exon":
            store=self.exons
        elif gffLine.feature=="start_codon":
            self.has_start_codon = True
            return
        elif gffLine.feature=="stop_codon":
            self.has_stop_codon = True
        else:
            raise loci_objects.exceptions.InvalidTranscript("Unknown feature: {0}".format(gffLine.feature))
            
        start,end=sorted([gffLine.start, gffLine.end])
        store.append((start, end) )

    def finalize(self):
        '''Function to calculate the internal introns from the exons.
        In the first step, it will sort the exons by their internal coordinates.'''
        
        # We do not want to repeat this step multiple times
        if self.finalized is True:
            return
        self.introns = []
        self.splices=[]
        if len(self.exons)==0:
            raise loci_objects.exceptions.InvalidTranscript("No exon defined for the transcript {0}. Aborting".format(self.tid))

        if len(self.exons)>1 and self.strand is None:
            raise loci_objects.exceptions.InvalidTranscript("Multiexonic transcripts must have a defined strand! Error for {0}".format(self.id))

        if self.combined_utr!=[] and self.combined_cds==[]:
            raise loci_objects.exceptions.InvalidTranscript("Transcript {tid} has defined UTRs but no CDS feature!".format(tid=self.id))

        if not (self.combined_cds_length==self.combined_utr_length==0 or  self.cdna_length == self.combined_utr_length + self.combined_cds_length):
            print("Assertion error", "\n".join(
                       [str(x) for x in [self.id, self.cdna_length, self.combined_utr_length, self.combined_cds_length,self.combined_utr, self.combined_cds, self.exons]]
                       )
             )
            raise loci_objects.exceptions.InvalidTranscript
            

        self.exons = sorted(self.exons, key=operator.itemgetter(0,1) ) # Sort the exons by start then stop
#         assert len(self.exons)>0
        try:
            if self.exons[0][0]!=self.start or self.exons[-1][1]!=self.end:
                raise loci_objects.exceptions.InvalidTranscript("The transcript {id} has coordinates {tstart}:{tend}, but its first and last exons define it up until {estart}:{eend}!".format(
                                                                                                                                                            tstart=self.start,
                                                                                                                                                            tend=self.end,
                                                                                                                                                            id=self.id,
                                                                                                                                                            eend=self.exons[-1][1],
                                                                                                                                                            estart=self.exons[0][0],
                                                                                                                                                            ))
        except IndexError as err:
            raise loci_objects.exceptions.InvalidTranscript(err, self.id, str(self.exons))

        if len(self.exons)>1:
            for index in range(len(self.exons)-1):
                exonA, exonB = self.exons[index:index+2]
                if exonA[1]>=exonB[0]:
                    print(exonA,exonB,self.id,self.exons)
                    raise loci_objects.exceptions.InvalidTranscript("Overlapping exons found!\n{0}\n{1}".format(self.id, self.exons))
                self.introns.append( (exonA[1]+1, exonB[0]-1) ) #Append the splice junction
                self.splices.extend( [exonA[1]+1, exonB[0]-1] ) # Append the splice locations

        self.combined_cds = sorted(self.combined_cds, key=operator.itemgetter(0,1))
        self.combined_utr = sorted(self.combined_utr, key=operator.itemgetter(0,1))
        if len(self.combined_utr)>0 and self.combined_utr[0][0]<self.combined_cds[0][0]:
            if self.strand=="+": self.has_start_codon=True
            elif self.strand=="-": self.has_stop_codon=True
        if len(self.combined_utr)>0 and self.combined_utr[-1][1]>self.combined_cds[-1][1]:
            if self.strand=="+": self.has_stop_codon=True
            elif self.strand=="-": self.has_start_codon=True
        
        self.internal_orfs = []
        self.segments = [ ("exon",e[0],e[1]) for e in self.exons] + \
                    [("CDS", c[0],c[1]) for c in self.combined_cds ] + \
                    [ ("UTR", u[0], u[1]) for u in self.combined_utr ]
        self.segments =  sorted(self.segments, key=operator.itemgetter(1,2,0) )
                
        self.internal_orfs.append(self.segments)
        if self.combined_cds_length>0:
            self.selected_internal_orf_index=0
        
        self.introns = set(self.introns)
        self.splices = set(self.splices)            
        _ = self.selected_internal_orf
#         assert self.selected_internal_orf_index > -1
        if len(self.combined_cds)>0:
            self.feature="mRNA"
        
        self.set_relative_properties()
        
        self.finalized = True
        return

    def set_relative_properties(self):
        '''Function to set to the basic value relative values like e.g. retained_intron'''
        self.retained_introns=[]
        self.retained_fraction=0
        self.exon_fraction=1
        self.intron_fraction=1
        self.cds_intron_fraction=1
        self.selected_cds_intron_fraction=1

    def reverse_strand(self):
        '''Method to reverse the strand'''
        if self.strand=="+":
            self.strand="-"
        elif self.strand=="-":
            self.strand="+"
        elif self.strand is None:
            pass
        return


    def connect_to_db(self):
        
        '''This method will connect to the database using the information contained in the JSON configuration.'''
        
        db = self.json_dict["db"]
        dbtype = self.json_dict["dbtype"]
        
        self.engine = create_engine("{dbtype}:///{db}".format(
                                                              db=db,
                                                              dbtype=dbtype)
                                    )   #create_engine("sqlite:///{0}".format(args.db))
        
        session = sessionmaker()
        session.configure(bind=self.engine)
        self.session=session()
        
    def load_information_from_db(self, json_dict):
        '''This method will invoke the check for:
        junctions
        orfs
        blast hits (this necessarily after the ORF check).
        '''
        
        self.load_json(json_dict)
        self.connect_to_db()
        self.load_junctions()
        self.load_orfs()
        self.load_blast()
        self.session.close() # The session must be closed
        
    def load_json(self, json_dict):
        self.json_dict = json_dict
    
    
    def load_junctions(self):
        
        '''This method will look up the junctions in the database and determine which ones are supported form external data.
        They will be stored inside the "verified_junctions" set.'''
        
        chrom_id=self.session.query(Chrom).filter(Chrom.name==self.chrom).one().id
        self.verified_junctions=[]
        junctions=self.session.query(junction.junctionStart,junction.junctionEnd).filter(and_(
                                                junction.chrom_id==chrom_id,
                                                junction.junctionStart>=self.start,
                                                junction.junctionEnd<=self.end
                                                 )
                                                ).all()
        for junction in junctions:
            tup=(junction.junctionStart,junction.junctionEnd) 
            if tup in self.introns:
                self.verified_junctions.append(tup)
                                              
        self.verified_junctions=set(self.verified_junctions)
        
        
    def load_orfs(self):
        
        '''This method will look up the ORFs loaded inside the database.
        Optional arguments:
        - minimal_secondary_orf_length:    Integer. When a transcript has multiple ORFs, ignore those which are shorter than this value.
                                           By default, it is set at 0.
        - trust_strand:            Boolean. Used to determine whether we trust the strand in the GFF or not.
        
        This function is used to load the various CDSs from an external database.
        It replicates what is done internally by the "cdna_alignment_orf_to_genome_orf.pl" utility in the
        TransDecoder suite.
        The method expects as argument a dictionary containing BED entries, and indexed by the transcript name.
        The indexed name *must* equal the "id" property, otherwise the method returns immediately. 
        If no entry is found for the transcript, the method exits immediately. Otherwise, any CDS information present in
        the original GFF/GTF file is completely overwritten.
        Briefly, it follows this logic:
        - Finalise the transcript
        - Retrieve from the dictionary (input) the CDS object
        - Sort in decreasing order the CDSs on the basis of:
            - Presence of start/stop codon
            - CDS length (useful for monoexonic transcripts where we might want to set the strand)
        - For each CDS:
            - If the ORF is on the + strand:
                - all good
            - If the ORF is on the - strand:
                - if the transcript is monoexonic: invert its genomic strand
                - if the transcript is multiexonic: skip
            - Start looking at the exons
       
        '''
        self.finalize()
        minimal_secondary_orf_length=self.json_dict["orf_loading"]["minimal_secondary_orf_length"]
        trust_strand = self.json_dict["orf_loading"]["trust_strand"]
        query_id=self.session.query(Query).filter(Query.name==self.id) #recover the id
        if query_id.count()==0: # if we do not have the information in the DB, just return
            return
        orf_query = self.session.query(orf).filter(orf.query_id==query_id)
        if orf_query.count()==0: # Again, no information, return
            return
        self.combined_utr = []
        self.combined_cds = []
        self.internal_orfs = []
        
        self.finalized = False
        primary_orf=True # Token to be set to False after the first CDS is exhausted
        candidate_orfs = orf_query.order_by(desc(orf.cds_len)).all()
        if (self.monoexonic is False) or (self.monoexonic is True and trust_strand is True):
            #Remove negative strand ORFs for multiexonic transcripts, or monoexonic strand-specific transcripts
            candidate_orfs=list(filter(lambda orf: orf.strand!="-", candidate_orfs  ))
        
        candidate_cliques=self.find_overlapping_cds(candidate_orfs)
        new_orfs=[]
        for clique in candidate_cliques:
            new_orfs.append(sorted(clique, reverse=True, key=operator.attrgetter("cds_len"))[0])

        candidate_orfs=[]
        if len(new_orfs)>0:
            candidate_orfs=[new_orfs[0]]
            for orf in filter(lambda x: x.cds_len>minimal_secondary_orf_length, new_orfs[1:]):
                candidate_orfs.append(orf)
        
        del new_orfs
        
        self.loaded_bed12 = [] #This will keep in memory the original BED12 objects
        
        for orf in candidate_orfs:
            #Minimal check
            if primary_orf is True:
                self.has_start_codon, self.has_stop_codon = orf.has_start_codon, orf.has_stop_codon
            
            if not (orf.thickStart>=1 and orf.thickEnd<=self.cdna_length):
                continue
            if self.strand is None:
                self.strand=new_strand=orf.strand
            primary_orf = False

            
            if self.strand is None: #Must be monoexonic
                self.strand=new_strand=orf.strand
            elif orf.strand == "-":
                assert self.monoexonic is True
                if new_strand is not None and orf.strand!=new_strand:
                        continue #Skip
                if self.strand=="+":
                    self.strand="-"
                elif self.strand=="-":
                    self.strand="+"

            self.loaded_bed12.append(orf)
            cds_exons = []
            current_start, current_end = 0,0
            if self.strand == "+":
                for exon in sorted(self.exons, key=operator.itemgetter(0,1)):
                    cds_exons.append(("exon", exon[0], exon[1] ) )
                    current_start+=1
                    current_end+=exon[1]-exon[0]+1
                    #Whole UTR
                    if current_end<orf.thickStart or current_start>orf.thickEnd:
                        cds_exons.append( ("UTR", exon[0], exon[1])  )
                    else:
                        c_start = exon[0] + max(0, orf.thickStart-current_start )
                        if c_start > exon[0]:
                            u_end = c_start-1
                            cds_exons.append( ("UTR", exon[0], u_end) )
                        c_end = exon[1] - max(0, current_end - orf.thickEnd )
#                         assert c_end>=exon[0] 
                        if c_start<c_end:
                            cds_exons.append(("CDS", c_start, c_end))
                        if c_end < exon[1]:
                            cds_exons.append( ("UTR", c_end+1, exon[1]  ) )
                    current_start=current_end
                            
            elif self.strand=="-":
                for exon in sorted(self.exons, key=operator.itemgetter(0,1), reverse=True):
                    cds_exons.append(("exon", exon[0], exon[1] ) )
                    current_start+=1
                    current_end+=exon[1]-exon[0]+1
                    if current_end<orf.thickStart or current_start>orf.thickEnd:
                        cds_exons.append( ("UTR", exon[0], exon[1] ))
                    else:
                        c_end = exon[1] - max(0,orf.thickStart - current_start ) 
#                         assert c_end>=exon[0]
                        if c_end < exon[1]:
                            cds_exons.append(("UTR", c_end+1, exon[1]))
                        c_start = exon[0] + max(0, current_end - orf.thickEnd )
                        cds_exons.append( ("CDS", c_start, c_end) )
                        if c_start>exon[0]:
                            cds_exons.append( ("UTR", exon[0], c_start-1) )
                    current_start=current_end
        
            self.internal_orfs.append( sorted(cds_exons, key=operator.itemgetter(1,2)   ) )

        if len(self.internal_orfs)==1:
            self.combined_cds = sorted(
                              [(a[1],a[2]) for a in filter(lambda x: x[0]=="CDS", self.internal_orfs[0])],
                              key=operator.itemgetter(0,1)
                              
                              )
            self.combined_utr = sorted(
                              [(a[1],a[2]) for a in filter(lambda x: x[0]=="UTR", self.internal_orfs[0])],
                              key=operator.itemgetter(0,1)
                              
                              )
            
            
        elif len(self.internal_orfs)>1:
            
            cds_spans = []
            candidates = []
            for internal_cds in self.internal_orfs:
                candidates.extend([tuple([a[1],a[2]]) for a in filter(lambda tup: tup[0]=="CDS", internal_cds  )])
                              
            candidates=set(candidates)
            #for mc in self.merge_cliques(*self.find_cliques(candidates)):
            for mc in self.find_communities(candidates):
                span=tuple([min(t[0] for t in mc),
                            max(t[1] for t in mc)                        
                            ])
                cds_spans.append(span)
 
            self.combined_cds = sorted(cds_spans, key = operator.itemgetter(0,1))
            
            #This method is probably OBSCENELY inefficient, but I cannot think of a better one for the moment.
            curr_utr_segment = None

            utr_pos = set.difference( 
                                                  set.union(*[ set(range(exon[0],exon[1]+1)) for exon in self.exons]),
                                                  set.union(*[ set(range(cds[0],cds[1]+1)) for cds in self.combined_cds])
                                                  )
            for pos in sorted(list(utr_pos)):
                if curr_utr_segment is None:
                    curr_utr_segment = (pos,pos)
                else:
                    if pos==curr_utr_segment[1]+1:
                        curr_utr_segment = (curr_utr_segment[0],pos)
                    else:
                        self.combined_utr.append(curr_utr_segment)
                        curr_utr_segment = (pos,pos)
                        
            if curr_utr_segment is not None:
                self.combined_utr.append(curr_utr_segment)           
                                   
            assert self.cdna_length == self.combined_cds_length + self.combined_utr_length, (self.cdna_length, self.combined_cds, self.combined_utr)                            
        
        if self.internal_orfs == []:
            self.finalize()
        else:
            self.feature="mRNA"
            self.finalized=True
        return
        

    def load_blast(self, maximum_number_hits = float("Inf"), maximum_evalue=10, max_hits=float("Inf")):
        
        '''This function will look into the DB for hits corresponding to the desired requirements.
        Hits will be loaded into the "blast_hits" list.
        '''
        
        
        self.blast_hits = []
        query_id = self.session.query(Query).filter(Query.name==self.id)
        if query_id.count()==0:
            return
        
        #Retrieve all hits for the query, order by evalue         
        blast_hits_query = self.session.query(Hit).filter(and_(Hit.query_id == query_id,
                                                               Hit.evalue<=maximum_evalue)
                                                          ).order_by(
                                                                     asc(Hit.evalue)
                                                                     )
        if blast_hits_query.count()==0:
            return
        if max_hits<float("Inf"):
            self.blast_hits = blast_hits_query.limit(max_hits).all()
        else:
            self.blast_hits = blast_hits_query.all()

    def load_cds(self, cds_dict, trust_strand=False, minimal_secondary_orf_length=0):
        '''Deprecated'''
        raise NotImplementedError()
                        

    def split_by_cds(self):
        '''This method is used for transcripts that have multiple ORFs. It will split them according to the CDS information
        into multiple transcripts. UTR information will be retained only if no ORF is down/upstream.
        The minimal overlap is defined inside the JSON at the key ["chimera_split"]["minimal_hsp_overlap"]
        - basically, we consider a HSP a hit only if the overlap is over a certain threshold and the HSP evalue under a certain threshold. 
        '''
        self.finalize()
        minimal_overlap=self.json_dict["chimera_split"]["minimal_hsp_overlap"]
        new_transcripts=[]
        if self.number_internal_orfs<2:
            new_transcripts=[self]
        else:
            to_split=True
            if self.json_dict["chimera_split"]["blast_check"] is True:
                
                cds_boundaries=[]
                for orf in self.loaded_bed12:
                    cds_boundaries.append((orf.thickStart,orf.thickEnd))
                cds_boundaries=sorted(cds_boundaries, key=operator.itemgetter(0,1))
                
                #At the moment the BLAST check is minimal .. if I can find a hit in the database such that
                #all the ORFs are found between the boundaries of its alignment, then I return True.
                #Otherwise, I continue splitting.
                #This is NON-OPTIMAL as I could have a situation where I merge two loci and TD finds three ORFs
                #Splitting one of the ORFs in two. 
                
                cds_hit_dict=defaultdict(set).fromkeys(cds_boundaries)

                for hit in self.blast_hits:
                    if hit.query_start<=cds_boundaries[0][0] and hit.query_end>=cds_boundaries[-1][1]:
                        to_split=False
                        break
                    for hsp in filter(lambda hsp: hsp.hsp_evalue<=self.json_dict["chimera_split"]["maximal_hsp_evalue"],  hit.hsps):
                        for cds_run in cds_boundaries:
                            if abstractlocus.overlap(cds_run, (hsp.query_hsp_start,hsp.query_hsp_end))>=minimal_overlap*(cds_run[1]+1-cds_run[0]):
                                cds_hit_dict[cds_run].add(hit.target)
                if to_split is True:
                    # Check whether there exists at least one couple of ORFs for which we have at least one target in common.
                    # If this eventuality verifies, the transcript should not be broken up.
                    to_split = not any(list(itertools.starmap(
                                                          lambda x,y: set.intersection(x,y)!=set(),
                                                          itertools.combinations(cds_hit_dict.values(), 2)
                                                          )))
            if to_split is True:
                orf_boundaries = []
                for orf in self.internal_orfs:
                    cds = sorted(list(filter(lambda x: x[0]=="CDS", orf)), key=operator.itemgetter(1,2))
                    orf_boundaries.append((cds[0][1],cds[-1][2]))
                
                counter=0
                zipper=list(zip(self.internal_orfs, self.loaded_bed12))
                
                for (orf,loaded_bed12) in zipper:
                    counter+=1
                    new_transcript=self.__class__()
                    for attribute in ["chrom", "source", "score","strand", "attributes" ]:
                        setattr(new_transcript,attribute, getattr(self, attribute))
                    new_transcript.has_start_codon = loaded_bed12.has_start_codon
                    new_transcript.has_stop_codon = loaded_bed12.has_stop_codon
                    new_transcript.id = "{0}.orf{1}".format(self.id,counter)
                    cds_segments=[(x[1],x[2]) for x in sorted(list(filter(lambda x: x[0]=="CDS", orf)), key=operator.itemgetter(1,2))]
                    assert len(cds_segments)>0
                    my_exons=[]
                    my_utr=[]
                    partials=[]
                    #Add full CDS exons to the exons to be added to the transcript
                    for seg in cds_segments:
                        if seg in self.exons:
                            my_exons.append((seg[0],seg[1]))
                        else:
                            partials.append((seg[0],seg[1]))
                    my_exons=sorted(my_exons,key=operator.itemgetter(0,1))
                    partials=sorted(partials,key=operator.itemgetter(0,1))
                    my_boundaries = (cds_segments[0][0],cds_segments[-1][1])
                    other_orfs = list(filter(lambda x: x!=my_boundaries, orf_boundaries))
                    left_orfs = list(filter(lambda x: x[1]<=my_boundaries[0], other_orfs)) # ORFs on the left of my chosen ORF 
                    right_orfs = list(filter(lambda x: x[0]>=my_boundaries[1], other_orfs)) # ORFs on the right of my chosen ORF
                    if len(left_orfs)==0 and len(right_orfs)==0:
                        raise loci_objects.exceptions.InvalidTranscript("I have not found any ORFs at my sides - this should not be possible!\n{0}\n{1}\n{2}".format(self.id, orf_boundaries, my_boundaries))
                    elif len(left_orfs)>0 and len(right_orfs)>0:
                        my_exons.extend(partials)
                        partials=[]
                    elif len(right_orfs)>0 and len(left_orfs)==0: #we have orfs on the RIGHT
                        left_exons = list(filter( lambda exon: exon[0]<my_boundaries[0], self.exons))
                        for exon in left_exons:
                            if exon[1]<my_boundaries[0]:
                                my_utr.append(exon)
                                my_exons.append(exon)
                            elif exon[1]==my_boundaries[0]:
                                my_utr.append((exon[0],exon[1]-1))
                                my_exons.append(exon)
                                partials.remove((exon[1],exon[1]))
                            elif exon[1]>my_boundaries[0]:
                                if exon[1]<=my_boundaries[1]:
                                    partial = list(filter(lambda x: x[1]==exon[1], partials))
                                    assert  len(partial)==1, (exon, partials, partial)
                                    partial=partial[0]
                                    partials.remove(partial)
                                    utr=(exon[0], partial[0]-1)
                                    my_utr.append(utr)
                                    my_exons.append(exon)
                                elif exon[1]>my_boundaries[1]:
                                    assert len(partials)==1
                                    partial=partials[0]
                                    partials=[]
                                    my_utr.append((exon[0], partial[0]-1))
                                    my_exons.append((exon[0], partial[1]))
                    elif len(left_orfs)>0 and len(right_orfs)==0: #We have ORFs on the left
                        right_exons = list(filter( lambda exon: exon[1]>my_boundaries[1], self.exons))
                        for exon in right_exons:
                            if exon[0]>my_boundaries[1]:
                                my_utr.append(exon)
                                my_exons.append(exon)
                            elif exon[0]==my_boundaries[1]:
                                my_utr.append((exon[0]+1,exon[1]))
                                my_exons.append(exon)
                                partials.remove((exon[0],exon[0]))
                            elif exon[0]<my_boundaries[1]:
                                if exon[0]>=my_boundaries[0]:
                                    partial = list(filter(lambda x: x[0]==exon[0], partials))
                                    assert  len(partial)==1, (exon, partials, partial)
                                    partial=partial[0]
                                    partials.remove(partial)
                                    utr=(partial[1]+1, exon[1])
                                    my_utr.append(utr)
                                    my_exons.append(exon)
                                elif exon[0]<my_boundaries[0]:
                                    assert len(partials)==1
                                    partial=partials[0]
                                    partials=[]
                                    my_utr.append((partial[1]+1,exon[1]))
                                    my_exons.append((partial[0], exon[1]))
    
                    my_exons.extend(partials)
                    my_exons=sorted(set(my_exons),key=operator.itemgetter(0,1))
                    new_transcript.exons=my_exons
                    new_transcript.combined_cds = sorted(cds_segments, key=operator.itemgetter(0,1))
                    new_transcript.combined_utr = sorted(my_utr, key=operator.itemgetter(0,1))
                    new_transcript.start = my_exons[0][0]
                    new_transcript.end = my_exons[-1][1]
                    new_transcript.finalize()
                    new_transcripts.append(new_transcript)
        assert len(new_transcripts)>0
        for nt in new_transcripts:
            yield nt
      
    @classmethod
    ####################Class methods#####################################  
    def find_cliques(cls, candidates):
        '''Wrapper for the abstractlocus method. It will pass to the function the class's "is_intersecting" method
        (which would be otherwise be inaccessible from the abstractlocus class method)'''
#         return abstractlocus.find_cliques( candidates, inters=cls.is_intersecting)
        raise NotImplementedError()
    
    @classmethod
    def find_overlapping_cds(cls, candidates):
        '''Wrapper for the abstractlocus method, used for finding overlapping ORFs.
        It will pass to the function the class's "is_overlapping_cds" method
        (which would be otherwise be inaccessible from the abstractlocus class method)'''
#         graph, cliques = abstractlocus.find_cliques( candidates, inters=cls.is_overlapping_cds)
        return abstractlocus.find_communities(candidates, inters=cls.is_overlapping_cds)
    
    
    @classmethod
    def is_overlapping_cds(cls, first, second):
        if first==second or first.strand!=second.strand or cls.overlap(
                                                                       (first.thickStart,first.thickEnd),
                                                                       (second.thickStart,second.thickEnd))<0:
            return False
        return True
        
    
    @classmethod
    def is_intersecting(cls, first, second):
        '''Implementation of the is_intersecting method.'''
        if first==second or cls.overlap(first, second)<0:
            return False
        return True

    @classmethod
    def overlap(cls, first,second):
        lend = max(first[0], second[0])
        rend = min(first[1], second[1])
        return rend-lend
    
    @classmethod
    def merge_cliques(cls, graph, cliques):
        '''Wrapper for the abstractlocus method.'''
        return abstractlocus.merge_cliques(graph,cliques)
    
    @classmethod
    def find_communities(cls, objects):
        '''Wrapper for the abstractlocus method.'''
        return abstractlocus.find_communities(objects, inters=cls.is_intersecting)

    
    @classmethod
    def get_available_metrics(cls):
        '''This function retrieves all metrics available for the class.'''
        metrics = list(x[0] for x in filter(lambda y: "__" not in y[0] and type(cls.__dict__[y[0]]) is metric, inspect.getmembers(cls)))
        assert "tid" in metrics and "parent" in metrics and "score" in metrics
        final_metrics = ["tid","parent","score"] + sorted(list(filter(lambda x: x not in ["tid","parent","score"], metrics )))  
        return final_metrics 

    ####################Class properties##################################

    @property
    def id(self):
        '''ID of the transcript - cannot be an undefined value.'''
        return self.__id
    
    @id.setter
    def id(self, Id):
        if type(Id) is not str:
            raise ValueError("Invalid value for id: {0}, type {1}".format(
                                                                          Id, type(Id)))
        self.__id = Id

    @property
    def available_metrics(self):
        return self.get_metrics()

    @property
    def strand(self):
        return self.__strand
    
    @strand.setter
    def strand(self,strand):
        if strand in ("+", "-"):
            self.__strand=strand
        elif strand in (None,".","?"):
            self.__strand=None
        else:
            raise ValueError("Invalid value for strand: {0}".format(strand))
        
    @property
    def selected_internal_orf(self):
        '''This property will return the tuple of tuples of the ORF selected as "best".
        To avoid memory wasting, the tuple is accessed in real-time using 
        a token (__max_internal_orf_index) which holds the position in the __internal_cds list of the longest CDS.'''
        if len(self.combined_cds)==0: # Non-sense to calculate the maximum CDS for transcripts without it
            self.__max_internal_orf_length=0
            self.selected_internal_orf_index=0
            return tuple([])
        else:
            return self.internal_orfs[self.selected_internal_orf_index]
    
    @property
    def selected_internal_orf_cds(self):
        '''This property will return the tuple of tuples of the CDS segments of the selected ORF
        inside the transcript. To avoid memory wasting, the tuple is accessed in real-time using 
        a token (__max_internal_orf_index) which holds the position in the __internal_cds list of the longest CDS.'''
        if len(self.combined_cds)==0: # Non-sense to calculate the maximum CDS for transcripts without it
            return tuple([])
        else:
            return list(filter(lambda x: x[0]=="CDS", self.internal_orfs[self.selected_internal_orf_index])) 

    @property
    def five_utr(self):
        '''Returns the exons in the 5' UTR of the selected ORF. If the start codon is absent, no UTR is given.'''
        if len(self.combined_cds)==0:
            return []
        elif self.has_start_codon is False:
            return []
        if self.strand=="+":
            return list(filter( lambda exon: exon[0]=="UTR" and exon[2]<self.selected_cds_start, self.selected_internal_orf  )  )
        elif self.strand=="-":
            return list(filter( lambda exon: exon[0]=="UTR" and exon[1]>self.selected_cds_start, self.selected_internal_orf  )  )

    @property
    def three_utr(self):
        '''Returns the exons in the 3' UTR of the selected ORF. If the end codon is absent, no UTR is given.'''
        if len(self.combined_cds)==0:
            return []
        elif self.has_stop_codon is False:
            return []
        if self.strand=="-":
            return list(filter( lambda exon: exon[0]=="UTR" and exon[2]<self.selected_cds_end, self.selected_internal_orf  )  )
        elif self.strand=="+":
            return list(filter( lambda exon: exon[0]=="UTR" and exon[1]>self.selected_cds_end, self.selected_internal_orf  )  )

    @property
    def selected_internal_orf_index(self):
        '''Token which memorizes the position in the ORF list of the selected ORF.'''
        return self.__max_internal_orf_index
    
    @selected_internal_orf_index.setter
    def selected_internal_orf_index(self, index):
        if index is None:
            self.__max_internal_orf_index = index
            return
        if type(index) is not int:
            raise TypeError()
        if index<0 or index>=len(self.internal_orfs):
            raise IndexError("No ORF corresponding to this index: {0}".format(index))
        self.__max_internal_orf_index = index
        
    @property
    def internal_orf_lengths(self):
        '''This property returns a list of the lengths of the internal ORFs.'''
        lengths = []
        for internal_cds in self.internal_orfs:
            lengths.append( sum( x[2]-x[1]+1 for x in filter(lambda c: c[0]=="CDS", internal_cds) ) )
        lengths = sorted(lengths, reverse=True)
        return lengths
        
    @property
    def non_overlapping_cds(self):
        '''This property returns a set containing the set union of all CDS segments inside the internal CDSs.
        In the case of a transcript with no CDS, this is empty.
        In the case where there is only one CDS, this returns the combined_cds holder.
        In the case instead where there are multiple CDSs, the property will calculate the set union of all CDS segments.'''
        if self.__non_overlapping_cds is None: 
            self.finalize()
            self.__non_overlapping_cds=set()
            for internal_cds in self.internal_orfs:
                segments = set([(x[1],x[2]) for x in filter(lambda segment: segment[0]=="CDS", internal_cds   )])
                self.__non_overlapping_cds.update(segments)
        return self.__non_overlapping_cds
    
    @non_overlapping_cds.setter
    def non_overlapping_cds(self,arg):
        '''Setter for the non_overlapping_cds property.'''
        self.__non_overlapping_cds = arg
    
    @property
    def exons(self):
        '''This property stores the exons of the transcript as (start,end) tuples.'''
        return self.__exons
    
    @exons.setter
    def exons(self, *args):
        if type(args[0]) not in (set,list):
            raise TypeError(type(args[0]))
        self.__exons=args[0]

    @property
    def combined_cds_introns(self):
        '''This property returns the introns which are located between CDS segments in the combined CDS.'''
        if len(self.combined_cds)<2:
            return []
        cintrons=[]
        all_cintrons=[]
        for position in range(len(self.combined_cds)-1):
            former=self.combined_cds[position]
            latter=self.combined_cds[position+1]
            junc=(former[1]+1,latter[0]-1)
            if junc in self.introns:
                cintrons.append(junc)
        if len(self.selected_cds_introns)>0:
            assert len(cintrons)>0,(self.id, self.selected_cds_introns,all_cintrons,self.introns) 
        cintrons=set(cintrons)
        assert type(cintrons) is set
        return cintrons

    @property
    def selected_cds_introns(self):
        '''This property returns the introns which are located between CDS segments in the selected ORF.'''
        cintrons=[]
        for position in range(len(self.selected_internal_orf_cds)-1):
            cintrons.append(
                            (self.selected_internal_orf_cds[position][1]+1, self.selected_internal_orf_cds[position+1][2]-1)
                            )
        cintrons=set(cintrons)
        return cintrons
    
    @property
    def combined_cds_start(self):
        '''This property returns the location of the start of the combined CDS for the transcript.
        If no CDS is defined, it defaults to the transcript start.'''
        if len(self.combined_cds)==0:
            if self.strand=="+":
                return self.start
            else:
                return self.end
        if self.strand=="+":
            return self.combined_cds[0][0]
        else:
            return self.combined_cds[-1][1]
       
    @property
    def selected_cds_start(self):
        '''This property returns the location of the start of the best CDS for the transcript.
        If no CDS is defined, it defaults to the transcript start.'''

        if len(self.combined_cds)==0:
            if self.strand=="+":
                return self.start
            else:
                return self.end
        if self.strand=="+":
            return self.selected_internal_orf_cds[0][1]
        else:
            return self.selected_internal_orf_cds[-1][2]

    @property
    def combined_cds(self):
        '''This is a list which contains all the non-overlapping CDS segments inside the cDNA.
        The list comprises the segments as duples (start,end).'''
        return self.__combined_cds

    @combined_cds.setter
    def combined_cds(self, *args):
        if type(args[0]) is not list or (len(args[0])>0 and len(list(filter(
                                                                            lambda x: len(x)!=2 or type(x[0]) is not int or type(x[1]) is not int, args[0])) )>0):
            raise TypeError("Invalid value for combined CDS: {0}".format(args[0]))
        self.__combined_cds = args[0]

    @property
    def combined_utr(self):
        '''This is a list which contains all the non-overlapping UTR segments inside the cDNA.
        The list comprises the segments as duples (start,end).'''
        return self.__combined_utr

    @combined_utr.setter
    def combined_utr(self, *args):
        if type(args[0]) is not list or (len(args[0])>0 and len(list(filter(
                                                                            lambda x: len(x)!=2 or type(x[0]) is not int or type(x[1]) is not int, args[0])) )>0):
            raise TypeError("Invalid value for combined CDS: {0}".format(args[0]))
        self.__combined_utr = args[0]



    @property
    def combined_cds_end(self):
        '''This property returns the location of the end of the combined CDS for the transcript.
        If no CDS is defined, it defaults to the transcript end.'''
        if len(self.combined_cds)==0:
            if self.strand=="+":
                return self.end
            else:
                return self.start
        if self.strand=="-":
            return self.combined_cds[0][0]
        else:
            return self.combined_cds[-1][1]

    @property
    def selected_cds_end(self):
        '''This property returns the location of the end of the best CDS for the transcript.
        If no CDS is defined, it defaults to the transcript start.'''

        if len(self.combined_cds)==0:
            if self.strand=="+":
                return self.end
            else:
                return self.start
        if self.strand=="-":
            return self.selected_internal_orf_cds[0][1]
        else:
            return self.selected_internal_orf_cds[-1][2]

    @property
    def monoexonic(self):
        if len(self.exons)==1:
            return True
        return False

    #################### Class metrics ##################################

    @metric
    def tid(self):
        '''ID of the transcript - cannot be an undefined value. Alias of id.'''
        return self.id
    
    @tid.setter
    def tid(self,tid):
        self.id=tid
        
    @metric
    def parent(self):
        '''Name of the parent feature of the transcript.'''
        return self.__parent
    
    @parent.setter
    def parent(self,parent):
        if type(parent) is list or parent is None:
            self.__parent=parent
        elif type(parent) is str:
            if "," in parent:
                self.__parent=parent.split(",")
            else:
                self.__parent=[parent]
        else:
            raise ValueError("Invalid value for parent: {0}, type {1}".format(
                                                                          parent, type(parent)))
            
    @metric
    def score(self):
        '''Numerical value which summarizes the reliability of the transcript.'''
        return self.__score
        
    @score.setter
    def score(self,score):
        
        if score is not None:
            if type(score) not in (float,int):
                try:
                    score=float(score)
                except:
                    raise ValueError("Invalid value for score: {0}, type {1}".format(
                                                                          score, type(score)))
        self.__score=score
                
        

    @metric
    def combined_cds_length(self):
        '''This property return the length of the CDS part of the transcript.'''
        return sum([ c[1]-c[0]+1 for c in self.combined_cds ])
    
    @metric
    def combined_cds_num(self):
        '''This property returns the number of non-overlapping CDS segments in the transcript.'''
        return len( self.combined_cds )

    @metric
    def combined_cds_num_fraction(self):
        '''This property returns the fraction of non-overlapping CDS segments in the transcript
        vs. the total number of exons'''
        return len( self.combined_cds )/len(self.exons)

    @metric
    def combined_cds_fraction(self):
        '''This property return the percentage of the CDS part of the transcript vs. the cDNA length'''
        return self.combined_cds_length/self.cdna_length
    
    @metric
    def combined_utr_length(self):
        '''This property return the length of the UTR part of the transcript.'''
        return sum([ e[1]-e[0]+1 for e in self.combined_utr ])
    
    @metric
    def combined_utr_fraction(self):
        '''This property returns the fraction of the cDNA which is not coding according
        to any ORF. Complement of combined_cds_fraction'''
        return 1-self.combined_cds_fraction
        
    @metric
    def cdna_length(self):
        '''This property returns the length of the transcript.'''
        return sum([ e[1]-e[0]+1 for e in self.exons ])
    
    @metric
    def number_internal_orfs(self):
        '''This property returns the number of ORFs inside a transcript.'''
        return len(self.internal_orfs)

    @metric
    def selected_cds_length(self):
        '''This property calculates the length of the CDS selected as best inside the cDNA.'''
        if len(self.combined_cds)==0:
            self.__max_internal_orf_length=0
        else:
            self.__max_internal_orf_length=sum(x[2]-x[1]+1 for x in filter(lambda x: x[0]=="CDS", self.selected_internal_orf))
        return self.__max_internal_orf_length
    
    @metric
    def selected_cds_num(self):
        '''This property calculates the number of CDS exons for the selected ORF'''
        return len(list( filter(lambda exon: exon[0]=="CDS", self.selected_internal_orf) ))
    
    @metric
    def selected_cds_fraction(self):
        '''This property calculates the fraction of the selected CDS vs. the cDNA length.'''
        return self.selected_cds_length/self.cdna_length
    
    @metric
    def highest_cds_exons_num(self):
        '''Returns the number of CDS segments in the selected ORF (irrespective of the number of exons involved)'''
        return len(list(filter(lambda x: x[0]=="CDS", self.selected_internal_orf)))
    
    @metric
    def selected_cds_exons_fraction(self):
        '''Returns the fraction of CDS segments in the selected ORF (irrespective of the number of exons involved)'''
        return len(list(filter(lambda x: x[0]=="CDS", self.selected_internal_orf)))/len(self.exons)
    

    @metric
    def highest_cds_exon_number(self):
        '''This property returns the maximum number of CDS segments among the ORFs; this number
        can refer to an ORF *DIFFERENT* from the maximal ORF.'''
        cds_numbers = []
        for cds in self.internal_orfs:
            cds_numbers.append(len(list(filter(lambda x: x[0]=="CDS", cds))))
        return max(cds_numbers)
    
    @metric
    def selected_cds_number_fraction(self):
        '''This property returns the proportion of best possible CDS segments vs. the number of exons.
        See selected_cds_number.'''
        return self.selected_cds_number/self.exon_num

    @metric
    def cds_not_maximal(self):
        '''This property returns the length of the CDS excluded from the selected ORF.'''
        if len(self.internal_orfs)<2:
            return 0
        return self.combined_cds_length-self.selected_cds_length
    
    @metric
    def cds_not_maximal_fraction(self):
        '''This property returns the fraction of bases not in the selected ORF compared to
        the total number of CDS bases in the cDNA.'''
        if self.combined_cds_length==0:
            return 0
        else:
            return self.cds_not_maximal/self.combined_cds_length
    
    @metric
    def five_utr_length(self):
        '''Returns the length of the 5' UTR of the selected ORF.'''
        if len(self.combined_cds)==0:
            return 0
        return sum(x[2]-x[1]+1 for x in self.five_utr)
                            
    @metric
    def five_utr_num(self):
        '''This property returns the number of 5' UTR segments for the selected ORF.'''
        return len(self.five_utr)

    @metric
    def five_utr_num_complete(self):
        '''This property returns the number of 5' UTR segments for the selected ORF, considering only those which are complete exons.'''
        return len(list(filter(lambda utr: (utr[1],utr[2]) in self.exons, self.five_utr)  ))


    @metric
    def three_utr_length(self):
        '''Returns the length of the 5' UTR of the selected ORF.'''
        if len(self.combined_cds)==0:
            return 0
        return sum(x[2]-x[1]+1 for x in self.three_utr)
                            
    @metric
    def three_utr_num(self):
        '''This property returns the number of 3' UTR segments (referred to the selected ORF).'''
        return len(self.three_utr)

    @metric
    def three_utr_num_complete(self):
        '''This property returns the number of 3' UTR segments for the selected ORF, considering only those which are complete exons.'''
        return len(list(filter(lambda utr: (utr[1],utr[2]) in self.exons, self.three_utr)   ))


    @metric
    def utr_num(self):
        '''Returns the number of UTR segments (referred to the selected ORF).'''
        return len(self.three_utr+self.five_utr)

    @metric
    def utr_num_complete(self):
        '''Returns the number of UTR segments which are complete exons (referred to the selected ORF).'''
        return self.three_utr_num_complete+self.five_utr_num_complete


    @metric
    def utr_fraction(self):
        '''This property calculates the length of the UTR of the selected ORF vs. the cDNA length.'''
        return 1-self.selected_cds_fraction

    @metric
    def utr_length(self):
        '''Returns the sum of the 5'+3' UTR lengths'''
        return self.three_utr_length+self.five_utr_length
    
    @metric
    def has_start_codon(self):
        '''Boolean. True if the selected ORF has a start codon.'''
        return self.__has_start
    
    @has_start_codon.setter
    def has_start_codon(self, *args):
        if args[0] not in (None, False,True):
            raise TypeError("Invalid value for has_start_codon: {0}".format(type(args[0])))
        self.__has_start=args[0]
        
    @metric
    def has_stop_codon(self):
        '''Boolean. True if the selected ORF has a stop codon.'''
        return self.__has_stop
    
    @has_stop_codon.setter
    def has_stop_codon(self, *args):
        if args[0] not in (None, False,True):
            raise TypeError("Invalid value for has_stop_codon: {0}".format(type(args[0])))
        self.__has_stop=args[0]

    @metric
    def is_complete(self):
        '''Boolean. True if the selected ORF has both start and end.'''
        return self.has_start_codon and self.has_stop_codon

    @metric
    def exon_num(self):
        '''This property returns the number of exons of the transcript.'''
        return len(self.exons)
    
    @metric
    def exon_fraction(self):
        '''This property returns the fraction of exons of the transcript which are contained in the sublocus.
        If the transcript is by itself, it returns 1. Set from outside.'''
        
        return self.__exon_fraction
    
    @exon_fraction.setter
    def exon_fraction(self, *args):
        if type(args[0]) not in (float,int) or (args[0]<=0 or args[0]>1):
            raise TypeError("Invalid value for the fraction: {0}".format(args[0]))
        self.__exon_fraction=args[0]
    
    @metric
    def intron_fraction(self):
        '''This property returns the fraction of introns of the transcript vs. the total number of introns in the locus.
        If the transcript is by itself, it returns 1. Set from outside.'''
        return self.__intron_fraction
    
    @intron_fraction.setter
    def intron_fraction(self, *args):
        if type(args[0]) not in (float,int) or (args[0]<0 or args[0]>1):
            raise TypeError("Invalid value for the fraction: {0}".format(args[0]))
        if not self.monoexonic and args[0]==0:
            raise ValueError("It is impossible that the intron fraction is null when the transcript has at least one intron!")
        self.__intron_fraction=args[0]

    @metric
    def max_intron_length(self):
        '''This property returns the greatest intron length for the transcript.'''
        if len(self.introns)==0:
            return 0
        return max(intron[1]+1-intron[0] for intron in self.introns)

    @metric
    def start_distance_from_tss(self):
        '''This property returns the distance of the start of the combined CDS from the transcript start site.
        If no CDS is defined, it defaults to 0.'''
        if len(self.combined_cds)==0: return 0
        distance=0
        if self.strand=="+":
            for exon in self.exons:
                distance+=min(exon[1],self.combined_cds_start)-exon[0]+1
                if self.combined_cds_start<=exon[1]:break
        elif self.strand=="-":
            exons=self.exons[:]
            exons.reverse()
            for exon in exons:
                distance+=exon[1]+1-max(self.combined_cds_start,exon[0])
                if self.combined_cds_start>=exon[0]:break
        return distance
                
    @metric
    def selected_start_distance_from_tss(self):
        '''This property returns the distance of the start of the best CDS from the transcript start site.
        If no CDS is defined, it defaults to 0.'''
        if len(self.combined_cds)==0: return 0
        distance=0
        if self.strand=="+":
            for exon in self.exons:
                distance+=min(exon[1],self.selected_cds_start)-exon[0]+1
                if self.selected_cds_start<=exon[1]:break
        elif self.strand=="-":
            exons=self.exons[:]
            exons.reverse()
            for exon in exons:
                distance+=exon[1]+1-max(self.selected_cds_start,exon[0])
                if self.selected_cds_start>=exon[0]:break
        return distance

    @metric
    def end_distance_from_tes(self):
        '''This property returns the distance of the end of the combined CDS from the transcript end site.
        If no CDS is defined, it defaults to 0.'''
        if len(self.combined_cds)==0: return 0
        distance=0
        if self.strand=="-":
            for exon in self.exons:
                distance+=min(exon[1],self.combined_cds_end)-exon[0]+1
                if self.cds_end<=exon[1]:break
        elif self.strand=="-":
            exons=self.exons[:]
            exons.reverse()
            for exon in exons:
                distance+=exon[1]+1-max(self.combined_cds_end,exon[0])
                if self.cds_end>=exon[0]:break
        return distance
    
    @metric
    def selected_end_distance_from_tes(self):
        '''This property returns the distance of the end of the best CDS from the transcript end site.
        If no CDS is defined, it defaults to 0.'''
        if len(self.combined_cds)==0: return 0
        distance=0
        if self.strand=="-":
            for exon in self.exons:
                distance+=min(exon[1],self.selected_cds_end)-exon[0]+1
                if self.selected_cds_end<=exon[1]:break
        elif self.strand=="-":
            exons=self.exons[:]
            exons.reverse()
            for exon in exons:
                distance+=exon[1]+1-max(self.selected_cds_end,exon[0])
                if self.selected_cds_end>=exon[0]:break
        return distance

    @metric
    def combined_cds_intron_fraction(self):
        '''This property returns the fraction of CDS introns of the transcript vs. the total number of CDS introns in the locus.
        If the transcript is by itself, it returns 1.'''
        return self.__combined_cds_intron_fraction
    
    @combined_cds_intron_fraction.setter
    def combined_cds_intron_fraction(self, *args):
        if type(args[0]) not in (float,int) or (args[0]<0 or args[0]>1):
            raise TypeError("Invalid value for the fraction: {0}".format(args[0]))
        self.__combined_cds_intron_fraction=args[0]

    @metric
    def selected_cds_intron_fraction(self):
        '''This property returns the fraction of CDS introns of the selected ORF of the transcript vs. the total number of CDS introns in the locus
        (considering only the selected ORF).
        If the transcript is by itself, it should return 1.'''
        return self.__selected_cds_intron_fraction
    
    @selected_cds_intron_fraction.setter
    def selected_cds_intron_fraction(self, *args):
        if type(args[0]) not in (float,int) or (args[0]<0 or args[0]>1):
            raise TypeError("Invalid value for the fraction: {0}".format(args[0]))
        self.__selected_cds_intron_fraction=args[0]


    @metric
    def retained_intron_num(self):
        '''This property records the number of introns in the transcripts which are marked as being retained.
        See the corresponding method in the sublocus class.'''
        return len(self.retained_introns)
    
    @metric
    def retained_fraction(self):
        '''This property returns the fraction of the cDNA which is contained in retained introns.'''
        return self.__retained_fraction        

    @retained_fraction.setter
    def retained_fraction(self, *args):
        if type(args[0]) not in (float,int) or (args[0]<0 or args[0]>1):
            raise TypeError("Invalid value for the fraction: {0}".format(args[0]))
        self.__retained_fraction=args[0]
        
