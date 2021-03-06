# -*- coding: utf-8 -*-
# cldomain is a Common Lisp domain for the Sphinx documentation tool.
# Copyright (C) 2011-2014 Russell Sim <russell.sim@gmail.com>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
    sphinxcontrib.cldomain
    ~~~~~~~~~~~~~~~~~~~~~~

    The Common Lisp domain

"""
import re
import os
import sys
from os import path
import tempfile
import json
from collections import defaultdict
import operator
import subprocess
from StringIO import StringIO
from docutils import nodes
from docutils.statemachine import string2lines, StringList
import pprint

from sphinx import addnodes
from sphinx.util.console import red
from sphinx.locale import l_, _
from sphinx.roles import XRefRole
from sphinx.domains import Domain, ObjType
from sphinx.directives import ObjectDescription
from sphinx.util.nodes import make_refnode
from sphinx.util.compat import Directive
from sphinx.util.docfields import Field, GroupedField

__version__ = open(path.join(path.dirname(__file__),
                   "version.lisp-expr")).read().strip('"')


ALL_TYPES = ["macro", "function", "genericFunction",
             "setf", "variable", "type"]
upper_symbols = re.compile("([^a-z\s\"`]*[A-Z]{2,}[^a-z\s\"`:]*)($|\s)")

DOC_STRINGS = defaultdict(dict, {})
TYPES = defaultdict(dict, {})
ARGS = defaultdict(dict, {})
METHODS = defaultdict(dict, {})
SLOTS = defaultdict(dict, {})
USED_SYMBOLS = defaultdict(dict, {})

lambda_list_keywords = ["&allow-other-keys", "&key",
                        "&rest", "&aux", "&optional"]


def node_to_dict(node):
    name = getattr(node, 'tagname', node)
    if getattr(node, 'rawsource', None):
        return {name: node.rawsource}
    nodes = {name: []}
    for child in node.children:
        nodes[name].append(node_to_dict(child))
    return nodes


def debug_print(node):
    """Useful in pdb sessions"""
    node = node_to_dict(node)
    pprint.pprint(node)


def record_use(package, symbol_name, objtype):
    """Record unused package symbols."""
    symbol = symbol_name.upper()
    USED_SYMBOLS[package].setdefault(symbol, []).append(objtype)


def bool_option(arg):
    """Used to convert flag options to directives.  (Instead of
    directives.flag(), which returns None).
    """
    return True


# An almost exact copy of Peter Norvig's scheme parser
# http://norvig.com/lispy.html
def _read(s):
    "Read a Scheme expression from a string."
    return _read_from(_tokenize(s))


def _tokenize(s):
    "Convert a string into a list of tokens."
    return s.replace('(', ' ( ').replace(')', ' ) ').split()


def _read_from(tokens):
    "Read an expression from a sequence of tokens."
    if len(tokens) == 0:
        raise SyntaxError('unexpected EOF while reading')
    token = tokens.pop(0)
    if '(' == token:
        L = []
        while tokens[0] != ')':
            L.append(_read_from(tokens))
        tokens.pop(0)  # pop off ')'
        return L
    elif ')' == token:
        raise SyntaxError('unexpected )')
    else:
        return token
# end of http://norvig.com/lispy.html


def parse_specializer_symbol(symbol, package):
    """Parse symbols, for specializers"""
    symbol = symbol.upper()
    if symbol.startswith(":"):
        return "KEYWORD" + symbol
    # TODO (RS) this needs to be smarter what happens if there is an
    # internal symbol instead of an external one?
    if ":" not in symbol:
        return package + ":" + symbol
    return symbol


class desc_clparameterlist(addnodes.desc_parameterlist):
    """Node for a common lisp parameter list."""
    child_text_separator = ' '

# v is short for visit
# d is short for depart


def v_clparameterlist(self, node):
    self.first_param = True
    self.body.append(' ')
    self.body.append('(')
    self.param_separator = node.child_text_separator


def d_clparameterlist(self, node):
    self.body.append(')')


def v_latex_clparameterlist(self, node):
    self.body.append('}{')
    self.first_param = True
    self.param_separator = node.child_text_separator


def d_latex_clparameterlist(self, node):
    self.body.append('}{')


class desc_clparameter(addnodes.desc_parameter):
    """Node for a common lisp parameter item."""


def d_clparameter(self, node):
    pass


def v_html_clparameter(self, node):
    if self.body[-1] != ('('):
        self.body.append(self.param_separator)
    if node.hasattr('lambda_keyword'):
        self.body.append('<em class="lambda_keyword text-muted">')
    elif node.hasattr('keyword'):
        self.body.append('<em class="keyword text-muted">')
    elif not node.hasattr('noemph'):
        self.body.append('<em>')


def d_html_clparameter(self, node):
    if node.hasattr('lambda_keyword'):
        self.body.append('</em>')
    elif node.hasattr('keyword'):
        self.body.append('</em>')
    elif not node.hasattr('noemph'):
        self.body.append('</em>')


def v_latex_clparameter(self, node):
    if not self.first_param:
        self.body.append(self.param_separator)
    else:
        self.first_param = False
    if not node.hasattr('noemph'):
        self.body.append(r'\emph{')


def d_latex_clparameter(self, node):
    if not node.hasattr('noemph'):
        self.body.append('}')


def v_texinfo_clparameter(self, node):
    if not self.first_param:
        self.body.append(self.param_separator)
    else:
        self.first_param = False
    text = self.escape(node.astext())
    # replace no-break spaces with normal ones
    text = text.replace(u' ', '@w{ }')
    self.body.append(text)
    raise nodes.SkipNode


def v_text_clparameter(self, node):
    if not self.first_param:
        self.add_text(self.param_separator)
    else:
        self.first_param = False
    self.add_text(node.astext())
    raise nodes.SkipNode


def v_bs_html_desc_type(self, node):
    self.body.append(self.param_separator)
    self.body.append(self.starttag(node, 'tt', '', CLASS='desc-type'))


def d_bs_html_desc_type(self, node):
    self.body.append('</tt>')


def v_html_desc_type(self, node):
    self.body.append(self.param_separator)


def specializer(symbol, sexp, state, package, node_type=nodes.inline):
    result = StringIO()
    result.write("(")
    first = True
    for atom in sexp:
        if first:
            first = False
        else:
            result.write(" ")

        if atom.startswith("KEYWORD:"):
            result.write("(EQL :%s)" % atom.split(":")[-1])
        else:
            result.write(atom)
        result.write(" ")

    result.write(")")
    result.seek(0)

    xref = ':cl:generic:`%s <%s:%s>`' % \
           (result.read().lower(), package, symbol)
    lines = string2lines(xref)
    node = node_type()
    state.nested_parse(StringList(lines), 0, node)
    return node


def specializer_xref(symbol, sexp, state, package, node_type=nodes.inline):
    result = StringIO()
    first = True
    for atom in sexp:
        if first:
            first = False
        else:
            result.write(" ")

        if atom.startswith("KEYWORD:"):
            result.write("(EQL :%s)" % atom.split(":")[-1])
        elif package:
            if atom.startswith(package + ":"):
                result.write(atom.split(":")[-1])
            else:
                result.write(atom)
        else:
            result.write(atom)

    target = " ".join([a.lower() for a in sexp])
    node = node_type()
    result.seek(0)
    xref = ":cl:method:`(%s) <%s %s>`" % \
           (result.read().lower(), symbol, target)
    lines = string2lines(xref)
    state.nested_parse(StringList(lines), 0, node)
    return node


def qualify_sexp(package, sexp):
    """If the sexp contains atoms that don't have a package then qualify
    them.

    """
    sexp_ret = []
    for atom in sexp:
        if atom.startswith(":"):
            sexp_ret.append("keyword" + atom)
        elif ":" in atom:
            sexp_ret.append(atom)
        else:
            sexp_ret.append(package + ":" + atom)
    return sexp_ret


def fieldlist_index(node):
    """Find the index of a field list in a content node."""
    for i, n in enumerate(node):
        if isinstance(n, nodes.field_list):
            return i


def get_content_node(node):
    """Search through and find the content node from a signature."""
    for subnode in node:
        if isinstance(subnode, addnodes.desc):
            for subsubnode in subnode:
                if isinstance(subsubnode, addnodes.desc_content):
                    return subsubnode


class SpecializerField(Field):
    """
    """
    is_grouped = True
    list_type = nodes.bullet_list

    def __init__(self, name, names=(), label=None, rolename=None,
                 can_collapse=False):
        Field.__init__(self, name, names, label, True, rolename)
        self.can_collapse = can_collapse

    def make_field(self, domain, items):
        fieldname = nodes.field_name('', self.label)
        listnode = self.list_type()
        for content in items:
            par = nodes.paragraph()
            par += content
            listnode += nodes.list_item('', par)
        fieldbody = nodes.field_body('', listnode)
        return nodes.field('', fieldname, fieldbody)


class SEXP(object):
    def __init__(self, sexp, types=None, show_defaults=False):
        if not isinstance(sexp, list):
            self.sexp = _read(sexp)
        else:
            self.sexp = sexp
        self.types = types
        if self.types:
            for i, type in enumerate(self.types):
                type_node = addnodes.pending_xref(
                    '', refdomain='cl', reftype='type',
                    reftarget=type)
                # type = " " + type
                type_node += addnodes.desc_type(type, type)
                self.sexp[i] = [self.sexp[i], type_node]
        self.show_defaults = show_defaults
        self.show_defaults = True

    def as_parameterlist(self, function_name):
        return self.render_parameterlist(prepend_node=function_name)

    def render_parameterlist(self, signode=None, prepend_node=None, sexp=None):
        desc_sexplist = desc_clparameterlist()
        if prepend_node:
            desc_sexplist.append(prepend_node)
        if signode:
            signode.append(desc_sexplist)
        symbol = False
        for atom in sexp or self.sexp:
            if isinstance(atom, list):
                if self.show_defaults:
                    symbol = self.render_parameterlist(signode=desc_sexplist, sexp=atom)
                else:
                    symbol = self.render_atom(atom[0], desc_sexplist)
            else:
                symbol = self.render_atom(atom, desc_sexplist)
        return desc_sexplist

    def render_atom(self, token, signode, noemph=True):
        "add syntax hi-lighting to interesting atoms"
        if not isinstance(token, nodes.Element):
            param = desc_clparameter(token, token)
            if token.lower() in lambda_list_keywords:
                param["lambda_keyword"] = True
            if token.startswith(":"):
                param["keyword"] = True
        else:
            param = token
        signode.append(param)


class CLsExp(ObjectDescription):

    doc_field_types = [
        GroupedField('parameter', label=l_('Parameters'),
                     names=('param', 'parameter', 'arg', 'argument',
                            'keyword', 'kwparam')),
        Field('returnvalue', label=l_('Returns'), has_arg=False,
              names=('returns', 'return')),
    ]

    option_spec = {
        'nodoc': bool_option,
        'noindex': bool_option,
        'noinitargs': bool_option,
    }

    def handle_signature(self, sig, signode):
        symbol_name = []
        package = self.env.temp_data.get('cl:package')
        objtype = self.get_signature_prefix(sig)
        sig_split = sig.split(" ")
        sig = sig_split[0]
        signode.append(addnodes.desc_annotation(objtype, objtype))
        lisp_args = ARGS[package].get(sig.upper(), "")
        function_name = addnodes.desc_name(sig, sig)

        if not lisp_args.strip() and self.objtype in ["function"]:
            lisp_args = "()"
        if lisp_args.strip():
            types = []
            if self.objtype in ["method"]:
                types = self.arguments[0].split(' ')[1:]
            sexp = SEXP(lisp_args,
                        types=types,
                        show_defaults=self.env.app.config.cl_show_defaults)
            arg_list = sexp.as_parameterlist(function_name)
            signode.append(arg_list)
        else:
            signode.append(function_name)

        # Add Slots
        slots = SLOTS[package].get(sig.upper())
        if slots and "noinitargs" not in self.options:
            # TODO add slot details if describing a class
            for slot in slots:
                initarg = slot.get(u'initarg')
                if initarg and initarg.lower() != 'nil':
                    slotarg = addnodes.literal_emphasis(slot.get(u'name'), slot.get(u'name'))
                    slotsig = initarg.lower() + u' '
                    signode.append(addnodes.desc_optional(slotsig, slotsig, slotarg))

        symbol_name = sig
        if not symbol_name:
            raise Exception("Unknown symbol type for signature %s" % sig)
        record_use(package, symbol_name, self.objtype)
        return objtype.strip(), symbol_name

    def get_field_list(self, node):
        """Return the node's field list, if there isn't one then
        create it first."""
        # Add a field list if there isn't one
        if not node[1][-1].children:
            node[1][-1].append(nodes.field_list())
        if not isinstance(node[1][-1][0], nodes.field_list):
            node[1][-1].append(nodes.field_list())
        return node[1][-1][-1]

    def get_index_text(self, name, type):
        return _('%s (Lisp %s)') % (name.lower().split(":")[-1], type)

    def get_index_name(self, name, type):
        return type + ":" + name

    def get_signature_prefix(self, sig):
        return self.objtype + ' '

    def cl_symbol_name(self):
        return self.names[0][1].upper()

    def add_target_and_index(self, name, sig, signode):
        # node target
        type, name = name

        if 'cl:package' in self.env.temp_data:
            package = self.options.get(
                'module', self.env.temp_data.get('cl:package'))
            name = package.lower() + ":" + name
        else:
            return

        indexname = self.get_index_name(name, type)
        if name not in self.state.document.ids:
            signode['names'].append(name)
            signode['ids'].append(indexname)
            signode['first'] = (not self.names)
            self.state.document.note_explicit_target(signode)
            inv = self.env.domaindata['cl']['symbols']
            # TODO (RS) reenable this checking based on doc and type.
            # if name in inv:
            #     self.state_machine.reporter.warning(
            #         'duplicate symbol description of %s, ' % name +
            #         'other instance in ' + self.env.doc2path(inv[name][0]),
            #         line=self.lineno)
            if name in inv:
                inv[name].append((self.env.docname, self.objtype))
            else:
                inv[name] = [(self.env.docname, self.objtype)]

        indextext = self.get_index_text(name, type)
        if indextext:
            self.indexnode['entries'].append(('single', indextext, indexname, ''))

    def before_content(self):
        if "nodoc" in self.options:
            return
        package = self.env.temp_data.get('cl:package')
        name = self.names[0][1]
        if not package:
            self.state_machine.reporter.warning("No package specified for symbol %s." %
                                                name)
            return
        try:
            string = self.cl_doc_string()
        except KeyError:
            string = ""
            self.state_machine.reporter.warning("Can't find symbol %s:%s" %
                                                (package, name))
        if not string:
            return
        lines = string2lines(string) + ['']
        self.content = StringList(lines) + self.content

    def cl_doc_string(self, objtype=None):
        """Resolve a symbols doc string. Will raise KeyError if the symbol
        can't be found.

        """
        package = self.env.temp_data.get('cl:package')
        name = self.cl_symbol_name()
        objtype = objtype or self.objtype
        possible_strings = DOC_STRINGS[package][name]

        string = possible_strings.get(objtype, "")
        return string


class CLGeneric(CLsExp):

    option_spec = {
        'nodoc': bool_option,
        'noindex': bool_option,
        'nospecializers': bool_option,
    }

    def run_add_specializers(self, result):
        package = self.env.temp_data.get('cl:package')
        name = self.cl_symbol_name()
        specializers = METHODS[package].get(name, {}).keys()
        if specializers:
            spec = nodes.bullet_list()
            for s in specializers:
                spec_xref = specializer_xref(package + ":" + name, s,
                                             self.state, package)
                item = nodes.list_item('', spec_xref)
                spec.append(item)

            field_list = self.get_field_list(result)
            field_list.append(
                nodes.field('',
                            nodes.field_name('', "Specializers"),
                            nodes.field_body('', spec)))
        return result

    def run(self):
        result = super(CLGeneric, self).run()
        if "nospecializers" not in self.options:
            self.run_add_specializers(result)
        return result


class CLMethod(CLGeneric):

    option_spec = {
        'nodoc': bool_option,
        'noindex': bool_option,
        'noinherit': bool_option,
        'nospecializers': bool_option,
        'linkgeneric': bool_option,
    }

    doc_field_types = [
        Field('specializer', label=l_('Specializer'), has_arg=False,
              names=('specializer')),
        GroupedField('parameter', label=l_('Parameters'),
                     names=('param', 'parameter', 'arg', 'argument',
                            'keyword', 'kwparam')),
        Field('returnvalue', label=l_('Returns'), has_arg=False,
              names=('returns', 'return')),
    ]

    def get_index_name(self, name, type):
        package = self.env.temp_data.get('cl:package')
        specializer = self.arguments
        spec_args = qualify_sexp(package, specializer[0].split(" ")[1:])
        specializer = " ".join(spec_args)
        return type + ":" + name + "(" + specializer.lower() + ")"

    def get_index_text(self, name, type):
        specializer = self.arguments
        spec_args = specializer[0].split(" ")[1:]
        specializer = " ".join(spec_args)
        return _('%s (%s) (Lisp %s)') % (name.lower().split(":")[-1],
                                         specializer.lower(), type)

    def add_target_and_index(self, name, sig, signode):
        # node target
        type, name = name

        if 'cl:package' in self.env.temp_data:
            package = self.options.get(
                'module', self.env.temp_data.get('cl:package'))
            name = package.lower() + ":" + name
        else:
            return

        indexname = self.get_index_name(name, type)
        if name not in self.state.document.ids:
            signode['names'].append(name)
            signode['ids'].append(indexname)
            signode['first'] = (not self.names)
            self.state.document.note_explicit_target(signode)
            inv = self.env.domaindata['cl']['methods']
            # TODO (RS) reenable this checking based on doc and type.
            # if name in inv:
            #     self.state_machine.reporter.warning(
            #         'duplicate symbol description of %s, ' % name +
            #         'other instance in ' + self.env.doc2path(inv[name][0]),
            #         line=self.lineno)
            sig = " ".join(qualify_sexp(package.lower(), sig.split(" ")[1:]))  # trim method name
            if name in inv:
                inv[name][sig] = (self.env.docname, self.objtype)
            else:
                inv[name] = {sig: (self.env.docname, self.objtype)}

        indextext = self.get_index_text(name, type)
        if indextext:
            self.indexnode['entries'].append(('single', indextext, indexname, ''))

    def cl_doc_string(self):
        """Resolve a symbols doc string. Will raise KeyError if the symbol
        can't be found.

        """
        package = self.env.temp_data.get('cl:package')
        name = self.cl_symbol_name()

        specializer = self.arguments
        spec = specializer[0].split(" ")[1:]
        method_doc = METHODS[package].get(name, {})
        key = tuple([parse_specializer_symbol(sym, package)
                     for sym in spec])
        if key not in method_doc:
            self.state_machine.reporter.warning("Can't find method %s:%s specializer %s, available specializers are %s" %
                                           (package, name, key, method_doc.keys()))
        doc = method_doc.get(key, "")
        if doc:
            return doc

        if "noinherit" not in self.options:
            return super(CLMethod, self).cl_doc_string("generic")
        return ""

    def run(self):
        result = super(CLMethod, self).run()
        field_list = self.get_field_list(result)
        package = self.env.temp_data.get('cl:package')

        if "linkgeneric" in self.options:
            # TODO (RS) this will probably be removed in the future.
            spec = specializer(self.cl_symbol_name(),
                               self.arguments[0].split()[1:],
                               self.state,
                               package=package)

            field_list.append(
                nodes.field('',
                            nodes.field_name('', "Specializer"),
                            nodes.field_body('', spec)))

        return result


class CLCurrentPackage(Directive):
    """This directive is just to tell Sphinx that we're documenting stuff
    in namespace foo.

    """

    has_content = False
    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = True
    option_spec = {}

    def run(self):
        env = self.state.document.settings.env
        env.temp_data['cl:package'] = self.arguments[0].upper()
        #index_package(self.arguments[0].upper())
        return []


class CLXRefRole(XRefRole):
    def process_link(self, env, refnode, has_explicit_title, title, target):
        if not has_explicit_title:
            target = target.lstrip('~')  # only has a meaning for the title
            # if the first character is a tilde, don't display the package
            if title[0:1] == '~':
                symbol = title[1:].split(':')
                package = symbol[0]
                title = symbol[-1]
                if target[0] == ":":
                    title = ":" + title
        return title, target


class CLDomain(Domain):
    """CL language domain."""
    name = 'cl'
    label = 'Common Lisp'

    object_types = {
        'package': ObjType(l_('package'), 'package'),
        'function': ObjType(l_('function'), 'function'),
        'macro': ObjType(l_('macro'), 'macro'),
        'variable': ObjType(l_('variable'), 'variable'),
        'type': ObjType(l_('type'), 'type'),
        'generic': ObjType(l_('generic'), 'generic'),
        'method': ObjType(l_('method'), 'method'),
        }

    directives = {
        'package': CLCurrentPackage,
        'function': CLsExp,
        'generic': CLGeneric,
        'macro': CLsExp,
        'variable': CLsExp,
        'type': CLsExp,
        'method': CLMethod,
    }

    roles = {
        'symbol': CLXRefRole(),
        'function': CLXRefRole(),
        'generic': CLXRefRole(),
	'macro': CLXRefRole(),
	'variable': CLXRefRole(),
        'type': CLXRefRole(),
        'method': CLXRefRole(),
    }
    initial_data = {
        'symbols': {},
        'methods': {},
    }

    def clear_doc(self, docname):
        for fullname, docs in self.data['symbols'].items():
            for (fn, _) in docs:
                if fn == docname:
                    del self.data['symbols'][fullname]

    def find_obj(self, env, name):
        """Find a Lisp symbol for "name", perhaps using the given package
        Return a list of (name, object entry) tuples.

        """
        symbols = self.data['symbols']
        name = name.lower()
        if ":" in name:
            if name in symbols:
                return [(name, symbols[name])]
        else:
            def filter_symbols(symbol):
                symbol = symbol[0]
                if name == symbol:
                    return True
                if ":" in symbol:
                    symbol = symbol.split(":")[1]
                    if name == symbol:
                        return True
                return False
            return filter(filter_symbols, symbols.items())

    def find_method(self, env, name, node):
        """Find a Lisp symbol for "name", perhaps using the given package
        Return a list of (name, object entry) tuples.

        """
        methods = self.data['methods']
        name = name.lower()
        sexp = name.split(" ")
        generic = sexp[0]
        specializer = " ".join(sexp[1:])
        if generic in methods:
            if specializer in methods[generic]:
                return [methods[generic][specializer]]
            else:
                env.warn_node('can\'t find method %s' % (name), node)
        else:
            env.warn_node('can\'t find generic %s' % (name), node)

    def resolve_xref(self, env, fromdocname, builder,
                     typ, target, node, contnode):
        if " " in target:
            matches = self.find_method(env, target.upper(), node)
        else:
            matches = self.find_obj(env, target.upper())

        if not matches:
            return None
        elif len(matches) > 1:
            env.warn_node(
                'more than one target found for cross-reference '
                '%r: %s' % (target, ', '.join(match[0] for match in matches)),
                node)
        # TODO (RS) this just chooses the first symbol, instead every
        # symbol should be presented.

        if " " in target:
            sexp = target.split(" ")
            generic = sexp[0].lower()
            specializer = " ".join(sexp[1:])
            name = generic
            filename = matches[0][0]  # the first filename
            link = "method" + ":" + generic + "(" + specializer + ")"
        else:
            name = matches[0][0]  # the symbol name
            filename = matches[0][1][0][0]  # the first filename
            type = matches[0][1][0][1]  # the first type
            link = type + ":" + name
        return make_refnode(builder, fromdocname, filename,
                            link, contnode, name)

    def get_symbols(self):
        for refname, docs in self.data['symbols'].iteritems():
            for (docname, type) in docs:
                yield (refname, refname, type, docname, refname, 1)


def save_cldomain_output(output):
    """Save a copy of the clgit output for debugging."""
    fd, path = tempfile.mkstemp('.log', 'cldomain-err-')
    os.write(fd, output.encode('utf-8'))
    os.close(fd)
    return path


def index_packages(systems, system_paths, packages, quicklisp, lisps, cl_debug):
    """Call an external lisp program that will return a dictionary of doc
    strings for all public symbols.

    """
    cl_launch_exe = [which("cl-launch")[0]]
    cl_launch_command = cl_launch_args(lisps)
    cldomain_args = ["--"]
    for package in packages:
        cldomain_args.extend(["--package", package])
    for system in systems:
        cldomain_args.extend(["--system", system])
    for system_path in system_paths:
        cldomain_args.extend(["--path", system_path])
    env = os.environ.copy()
    env.update({"CLDOMAIN": path.abspath(path.dirname(__file__)) + "/",
                "QUICKLISP": quicklisp})
    raw_output = subprocess.check_output(cl_launch_exe
                                         + cl_launch_command
                                         + cldomain_args,
                                         env=env)
    output = "\n".join([line for line in raw_output.split("\n")
                        if not line.startswith(";")])

    try:
        lisp_data = json.loads(output)
        if cl_debug:
            pprint.pprint(lisp_data)
    except:
        dump_path = save_cldomain_output(raw_output)
        error = sys.stderr
        print >>error, red('A error occurred with the json output from cldomain\'s'
                           ' lisp inspector,  this has been dumped to %s if you '
                           'intend on submitting a bug please include this file '
                           'with the sphinx error log.' % dump_path)

        raise

    for k, v in lisp_data.items():
        symbol_name = k.split(':')
        package, name = symbol_name[0], symbol_name[-1]
        # extract doc strings
        DOC_STRINGS[package][name] = {}
        for type in ALL_TYPES:
            if type not in v:
                continue
            # XXX This isn't the best, the objtype is generic but the
            # docstring will be under genericFunction because of the JSON
            # encoder and changing the directive name doesn't seem to help
            # either.
            if type == "genericFunction":
                cl_type = "generic"
            else:
                cl_type = type

            # enable symbol references for symbols
            DOC_STRINGS[package][name][cl_type] = v[type]

        # extract methods
        if "methods" in v:
            def parse_method(method):
                sexp = []
                for atom in json.loads(method):
                    if atom.startswith("("):
                        eql = _read(atom)
                        sexp.append(eql[-1])
                    else:
                        sexp.append(atom)
                return tuple(sexp)

            def parse_doc(doc):
                if doc is None:
                    doc = ""
                return doc

            methods = dict([(parse_method(method), parse_doc(doc))
                            for method, doc in v["methods"].items()])
            METHODS[package][name] = methods

        # extract slots
        if "slots" in v:
            SLOTS[package][name] = v["slots"]

    def lower_symbols(text):
        if '"' in text:
            return text

        symbol_name = text.split(':')
        if len(symbol_name) > 1:
            spackage, symbol = symbol_name[0], symbol_name[-1]
        else:
            spackage = ''
            symbol = ''

        if spackage.upper() in packages:
            return symbol.lower()

        return text.lower()

    # extract arguments
    packages = map(operator.methodcaller('upper'), packages)
    for k, v in lisp_data.items():
        spackage, symbol = k.split(':')
        if not v.get("arguments"):
            pass
        elif v["arguments"] == "NIL":
            ARGS[spackage][symbol] = ""
        else:
            v_arg = v["arguments"].replace('(', ' ( ').replace(')', ' ) ')
            ARGS[spackage][symbol] = " ".join(map(lower_symbols,
                                                  v_arg.split(" ")))


def load_packages(app):
    packages = []
    systems = []
    system_paths = []
    if app.config.cl_packages:
        app.info("DEPRECATED: The cl_packages variable has been "
                 "replaced by cl_systems and will be removed in the future.")
        for package, system_path in app.config.cl_packages.iteritems():
            packages.append(package.upper())
            systems.append(package)
            system_paths.append(system_path)
    if app.config.cl_systems:
        for system in app.config.cl_systems:
            systems.append(system['name'])

            if 'path' in system:
                system_paths.append(system['path'])

            if 'packages' in system:
                for package in system['packages']:
                    packages.append(package.upper())
            else:
                packages.append(system['name'].upper())

    if not packages:
        app.warn("No CL packages specified.")
        return

    app.info("Collecting Lisp docstrings from %s..." % ', '.join(str(x) for x in systems))
    index_packages(systems,
                   system_paths,
                   packages,
                   app.config.cl_quicklisp,
                   app.config.cl_lisps,
                   app.config.cl_debug)


def uppercase_symbols(app, docname, source):
    """For each line in a list replace all uppercase symbols with a
    sphinx references"""
    for i, line in enumerate(source):
        source[i] = re.sub(upper_symbols,
                           ":cl:symbol:`~\g<1>`\g<2>", line)


def list_unused_symbols(app, exception):
    if exception:
        return
    # TODO (RS) this initial implementation will not be able to detect
    # if each method specialisation has been used.
    for p, sym_doc in DOC_STRINGS.items():
        for s, docs in sym_doc.items():
            for objtype in docs.keys():
                if s in USED_SYMBOLS[p]:
                    if objtype == "genericFunction":
                        objtype = "generic"
                    if objtype not in USED_SYMBOLS[p][s]:
                        app.warn("Unused symbol doc %s:%s type %s" %
                                           (p, s, objtype))
                else:
                    app.warn("Unused symbol doc %s:%s type %s" %
                                           (p, s, objtype))


def add_node(class_name, node, visit, depart=None):
    """Register a node's visitor functions with a class, if is available.

    """

    def import_class(cl):
        d = cl.rfind(".")
        classname = cl[d+1:len(cl)]
        m = __import__(cl[0:d], globals(), locals(), [classname])
        return getattr(m, classname)

    try:
        translator = import_class(class_name)
    except (ImportError, AttributeError):
        return
    setattr(translator, 'visit_' + node.__name__, visit)
    if depart:
        setattr(translator, 'depart_'+node.__name__, depart)

add_node('sphinx_bootstrap_theme.BootstrapTranslator',
         desc_clparameterlist,
         v_clparameterlist, d_clparameterlist)

add_node('sphinx_bootstrap_theme.BootstrapTranslator',
         desc_clparameter,
         v_html_clparameter, d_html_clparameter)

add_node('sphinx_bootstrap_theme.BootstrapTranslator',
         addnodes.desc_type,
         v_bs_html_desc_type, d_bs_html_desc_type)

add_node('sphinx.writers.html.HTMLTranslator',
         addnodes.desc_type,
         v_html_desc_type)


def setup(app):
    app.add_domain(CLDomain)
    app.add_node(desc_clparameterlist,
                 html=(v_clparameterlist, d_clparameterlist),
                 latex=(v_latex_clparameterlist, d_latex_clparameterlist),
                 texinfo=(v_clparameterlist, d_clparameterlist),
                 text=(v_clparameterlist, d_clparameterlist))
    app.add_node(desc_clparameter,
                 html=(v_html_clparameter, d_html_clparameter),
                 latex=(v_latex_clparameter, d_latex_clparameter),
                 texinfo=(v_texinfo_clparameter, d_clparameter),
                 text=(v_text_clparameter, d_clparameter))
    app.add_config_value('cl_packages', {}, 'env')
    app.add_config_value('cl_systems', {}, 'env')
    app.add_config_value('cl_quicklisp', path.expandvars("$HOME/quicklisp"), 'env')
    app.add_config_value('cl_show_defaults', False, True)
    app.add_config_value('cl_lisps', None, 'env')
    app.add_config_value('cl_debug', False, 'env')
    app.connect('builder-inited', load_packages)
    app.connect('build-finished', list_unused_symbols)
    #app.connect('source-read', uppercase_symbols)


def which(name, flags=os.X_OK):
    """https://twistedmatrix.com/trac/browser/tags/releases/twisted-8.2.0/twisted/python/procutils.py"""
    result = []
    exts = filter(None, os.environ.get('PATHEXT', '').split(os.pathsep))
    path = os.environ.get('PATH', None)
    if path is None:
        return []
    for p in os.environ.get('PATH', '').split(os.pathsep):
        p = os.path.join(p, name)
        if os.access(p, flags):
            result.append(p)
        for e in exts:
            pext = p + e
            if os.access(pext, flags):
                result.append(pext)
    return result


def cl_launch_args(lisps=None,
                   package='sphinxcontrib.cldomain',
                   main_function="sphinxcontrib.cldomain:main"):
    quicklisp = """
#-quicklisp
(let ((quicklisp-init (merge-pathnames (make-pathname :name "setup"
                                                      :type "lisp")
                                       (concatenate 'string (asdf/os:getenv "QUICKLISP")
                                                    "/"))))
  (if (probe-file quicklisp-init)
      (load quicklisp-init)
      (error "Can't Find Quicklisp at ~a~%" quicklisp-init)))
"""

    system = """
(push (pathname (concatenate 'string (asdf/os:getenv \"CLDOMAIN\") \"/\"))
                             asdf:*central-registry*)
"""

    quickload = """
(let ((*standard-output* *error-output*))
  (quicklisp:quickload '%s))
""" % package
    args = []
    if lisps:
        args.extend(["--lisp", lisps])
    args.extend(["--init", quicklisp,
                 "--init", system,
                 "--init", "(asdf:initialize-source-registry)",
		 "--init", "(asdf:require-system :quicklisp)",
                 "--init", quickload,
                 "--init", "(%s)" % main_function])
    return args
