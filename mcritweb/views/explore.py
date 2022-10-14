from flask import Blueprint, render_template, request, redirect, url_for, flash
from mcrit.client.McritClient import McritClient
from mcrit.storage.FamilyEntry import FamilyEntry
from mcrit.storage.SampleEntry import SampleEntry
from mcrit.storage.FunctionEntry import FunctionEntry

from mcritweb.views.authentication import visitor_required
from mcritweb.views.utility import get_server_url, mcrit_server_required
from mcritweb.views.cursor_pagination import CursorPagination

import mcritweb.views.cfg_explorer_detector as cfg_explorer_detector

bp = Blueprint('explore', __name__, url_prefix='/explore')


##############################################################
### Unfiltered Collections: Families, Samples, Function
##############################################################

@bp.route('/families')
@mcrit_server_required
@visitor_required
def families():
    family_id = request.args.get('family_id')
    if family_id is not None:
        return redirect(url_for('explore.family_by_id', family_id=family_id, p=request.args.get('p')))
    query = request.args.get('query', "")
    client = McritClient(mcrit_server= get_server_url())
    families = []
    pagination = CursorPagination(request, default_sort="family_id")
    results = client.search_families(query, **pagination.getSearchParams(), limit=50)
    pagination.read_cursor_from_result(results)
    if results is None:
        flash(f"Ups, search for {query} in MCRIT's families failed!", category="error")
    else:
        for family_dict in results['search_results'].values():
            families.append(FamilyEntry.fromDict(family_dict))
    return render_template("families.html", families=families, pagination=pagination, query=query)


@bp.route('/samples')
@mcrit_server_required
@visitor_required
def samples():
    sample_id = request.args.get('sample_id')
    if not sample_id is None:
        return redirect(url_for('explore.sample_by_id', sample_id=sample_id, p=request.args.get('p')))

    query = request.args.get('query', "")
    client = McritClient(mcrit_server= get_server_url())
    samples = []
    pagination = CursorPagination(request, default_sort="sample_id")
    results = client.search_samples(query, **pagination.getSearchParams(), limit=50)
    pagination.read_cursor_from_result(results)
    if results is None:
        flash(f"Ups, search for {query} in MCRIT's samples failed!", category="error")
    else:
        for sample_dict in results['search_results'].values():
            samples.append(SampleEntry.fromDict(sample_dict))

    return render_template("samples.html", samples=samples, pagination=pagination, query=query)


@bp.route('/functions')
@mcrit_server_required
@visitor_required
def functions():
    function_id = request.args.get('function_id')
    if not function_id is None:
        return redirect(url_for('explore.function_by_id', function_id=function_id, p=request.args.get('p')))
    query = request.args.get('query', "")
    client = McritClient(mcrit_server= get_server_url())
    functions = []
    pagination = CursorPagination(request, default_sort="function_id")
    results = client.search_functions(query, **pagination.getSearchParams(), limit=50)
    pagination.read_cursor_from_result(results)
    if results is None:
        flash(f"Ups, search for {query} in MCRIT's functions failed!", category="error")
    else:
        for function_dict in results['search_results'].values():
            #functions.append(FunctionEntry.fromDict(function_dict))
            functions.append(function_dict)
    return render_template("functions.html", functions=functions, pagination=pagination, query=query)

##############################################################
### Single Entries: Families, Samples, Function
##############################################################

@bp.route('/families/<int:family_id>')
@mcrit_server_required
@visitor_required
def family_by_id(family_id):
    client = McritClient(mcrit_server= get_server_url())
    family_info = client.getFamily(family_id, with_samples=False)
    if family_info:
        original_query = request.args.get('query', "")
        query = f"family_id:{family_id} {original_query}"
        client = McritClient(mcrit_server= get_server_url())
        samples = []
        pagination = CursorPagination(request, default_sort="sample_id")
        results = client.search_samples(query, **pagination.getSearchParams(), limit=50)
        pagination.read_cursor_from_result(results)
        if results is None:
            flash(f"Ups, search for {query} in MCRIT's samples failed!", category="error")
        else:
            for sample_dict in results['search_results'].values():
                samples.append(SampleEntry.fromDict(sample_dict))
        return render_template("single_family.html", family=family_info, samples=samples, pagination=pagination, query=original_query)
    else:
        flash("The given Family ID doesn't exist", category='error')
        return redirect(url_for('explore.families'))


@bp.route('/samples/<int(signed=True):sample_id>')
@visitor_required
@mcrit_server_required
def sample_by_id(sample_id):
    client = McritClient(mcrit_server= get_server_url())
    sample_entry = client.getSampleById(sample_id)
    if sample_entry:
        if sample_id < 0:
            return render_template("single_query_sample.html", entry=sample_entry)
        original_query = request.args.get('query', "")
        query = f"sample_id:{sample_id} {original_query}"
        client = McritClient(mcrit_server= get_server_url())
        functions = []
        pagination = CursorPagination(request, default_sort="function_id")
        results = client.search_functions(query, **pagination.getSearchParams(), limit=50)
        pagination.read_cursor_from_result(results)
        if results is None:
            flash(f"Ups, search for {query} in MCRIT's functions failed!", category="error")
        else:
            jobs = client.getQueueData(filter=sample_id)
            filtered_jobs = [job for job in jobs if '('+str(sample_id)+')' in job.parameters or '('+str(sample_id)+',' in job.parameters or ','+str(sample_id)+',' in job.parameters or ','+str(sample_id)+')' in job.parameters]
            for function_dict in results['search_results'].values():
                functions.append(FunctionEntry.fromDict(function_dict))
        return render_template("single_sample.html", entry=sample_entry, functions=functions, pagination=pagination, query=original_query, jobs=filtered_jobs)
    else:
        flash("The given Sample ID doesn't exist", category='error')
        return redirect(url_for('explore.samples'))


@bp.route('/functions/<int(signed=True):function_id>')
@visitor_required
@mcrit_server_required
def function_by_id(function_id):
    client = McritClient(mcrit_server=get_server_url())
    function_entry = client.getFunctionById(function_id)
    if function_entry:
        pichash_match_summary = client.getMatchesForPicHash(function_entry.pichash, summary=True)
        return render_template("single_function.html", entry=function_entry, pichash_match_summary=pichash_match_summary)
    else:
        flash("The given Function ID doesn't exist", category="error")
        return redirect(url_for('explore.functions'))

# helper for @bp.route('/functions/<int:function_id>')
@bp.route('/fetchDotGraph/<int(signed=True):function_id>', methods=['GET'])
@visitor_required
@mcrit_server_required
def fetchDotGraph(function_id):
    client = McritClient(mcrit_server=get_server_url())
    function_entry = client.getFunctionById(function_id, with_xcfg=True)
    if function_entry:
        smda_function = function_entry.toSmdaFunction()
        dot_graph = smda_function.toDotGraph()
        # TODO can possibly do this fixup in a better place
        pbh_by_offset = {pbh["offset"]: pbh for pbh in function_entry.picblockhashes}
        for smda_block in smda_function.getBlocks():
            needle = f',label="{smda_block.offset:x}'
            replacement = f',comment=""{needle}'
            if smda_block.offset in pbh_by_offset:
                replacement = f',comment="0x{pbh_by_offset[smda_block.offset]["hash"]:x}"{needle}'
            dot_graph = dot_graph.replace(needle, replacement)
        return dot_graph
    return ""

# helper for @bp.route('/functions/<int:function_id>')
@bp.route('/findLoops/', methods=['GET', 'POST'])
@visitor_required
@mcrit_server_required
def findLoops():
    out_str = ""
    if request.method=='POST':
        data = request.data
        data = data.decode("utf-8")
        out_str = cfg_explorer_detector.run(data)
    return out_str


# helper for @bp.route('/functions/<int:function_id>')
@bp.route('/getPicBlockMatches/<picblockhash>', methods=['GET'])
@visitor_required
@mcrit_server_required
def getPicBlockMatches(picblockhash):
    client = McritClient(mcrit_server=get_server_url())
    return client.getMatchesForPicBlockHash(int(picblockhash, 16), summary=True)

##############################################################
### Statistics + Search
##############################################################

@bp.route('/statistics')
@visitor_required
@mcrit_server_required
def statistics():
    client = McritClient(mcrit_server= get_server_url())
    stats = client.getStatus()
    return render_template("statistics.html", stats=stats)


@bp.route('/search')
@visitor_required
@mcrit_server_required
def search():
    query = request.args.get('query', None)
    types = request.args.getlist("type")
    if len(types) > 1:
        args = {**request.args}
        args["type"] = ",".join(types)
        return redirect(url_for("explore.search", **args))
    if "type" not in request.args:
        types = ["family", "sample", "function"]
    else:
        types = request.args["type"].split(",")
    if not query:
        return render_template("search.html", search_types=types)
    client = McritClient(mcrit_server= get_server_url())

    #TODO: show id/sha matches in extra place
    families = []
    family_pagination = None
    if 'family' in types:
        family_pagination = CursorPagination(request, query_param_prefix="family", default_sort="family_id")
        results = client.search_families(query, **family_pagination.getSearchParams(), limit=15)
        family_pagination.read_cursor_from_result(results)
        if results is None:
            flash(f"Ups, search for {query} in MCRIT's families failed!", category="error")
        else:
            id_match = results['id_match']
            if id_match is not None:
                family = FamilyEntry.fromDict(id_match)
                families.append(family)
            for family_entry in results['search_results'].values():
                family = FamilyEntry.fromDict(family_entry)
                families.append(family) 

    samples = []
    sample_pagination = None
    if 'sample' in types:
        sample_pagination = CursorPagination(request, query_param_prefix="sample", default_sort="sample_id")
        results = client.search_samples(query, **sample_pagination.getSearchParams(), limit=15)
        sample_pagination.read_cursor_from_result(results)
        if results is None:
            flash(f"Ups, search for {query} in MCRIT's samples failed!", category="error")
        else:
            sha_match = results['sha_match']
            if sha_match is not None:
                samples.append(SampleEntry.fromDict(sha_match))
            id_match = results['id_match']
            if id_match is not None:
                samples.append(SampleEntry.fromDict(id_match))
            for sample_dict in results['search_results'].values():
                samples.append(SampleEntry.fromDict(sample_dict))

    functions = []
    function_pagination = None
    if 'function' in types:
        function_pagination = CursorPagination(request, query_param_prefix="function", default_sort="function_id")
        results = client.search_functions(query, **function_pagination.getSearchParams(), limit=15)
        function_pagination.read_cursor_from_result(results)
        if results is None:
            flash(f"Ups, search for {query} in MCRIT's functions failed!", category="error")
        else:
            id_match = results['id_match']
            if id_match is not None:
                functions.append(FunctionEntry.fromDict(id_match))
            for function_dict in results['search_results'].values():
                functions.append(FunctionEntry.fromDict(function_dict))

    return render_template(
        "search.html",
        families=families,
        samples=samples,
        functions=functions,
        family_pagination=family_pagination,
        sample_pagination=sample_pagination,
        function_pagination=function_pagination,
        query=query,
        search_types=types,
    )