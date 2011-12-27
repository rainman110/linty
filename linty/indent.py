#!/usr/bin/env python
"""The implementation of the linty indentation checks."""

from __future__ import with_statement

__author__ = 'Manuel Holtgrewe <manuel.holtgrewe@fu-berlin.de>'

import logging
import sys

import violations as lv
import checks as lc

import clang.cindex as ci

# ============================================================================
# Global Indentation Related Code
# ============================================================================


def lengthExpandedTabs(s, to_idx, tab_width):
    l = 0
    for i in range(0, to_idx):
        if s[i] == '\t':
            l += (l / tab_width + 1)  * tab_width
        else:
            l += 1
    return l


class IndentLevel(object):
    """Encapsulates a set of acceptable indentation levels."""
    
    def __init__(self, indent=None, base=None, offset=None):
        assert (indent is not None) or (base is not None) or (offset is not None)
        self.levels = set()
        if indent is not None:
            self.levels.add(indent)
        else:
            assert (base is not None) and (offset is not None)
            for l in base.levels:
                self.levels.add(l + offset)
    
    def isMultilevel(self):
        return len(self.levels) > 1

    def accept(self, indent):
        ##print type(self), 'accept(), level=', self.levels, 'indent=', indent
        return indent in self.levels

    def gt(self, indent):
        return sorted(self.levels)[-1] > indent

    def addAcceptedIndent(self, level):
        if type(level) is IndentLevel:
            for i in level.levels:
                self.levels.add(i)
        else:
            self.levels.add(level)

    def __str__(self):
        return 'IndentLevel({%s})' % (', '.join(map(str, list(self.levels))))


# ============================================================================
# Basic And Generic Node Handlers
# ============================================================================


class IndentSyntaxNodeHandler(object):
    """Base class for node handlers in the IndentationCheck."""

    def __init__(self, indentation_check, handler_name, node, parent):
        self.indentation_check = indentation_check
        self.handler_name = handler_name
        self.node = node
        self.parent = parent
        self.config = indentation_check.config
        self.level = self._getLevelImpl()
        self.violations = indentation_check.violations
        self._token_set = None

    # ------------------------------------------------------------------------
    # Token Retrieval Related.
    # ------------------------------------------------------------------------

    def getFirstToken(self):
        """Returns first token."""
        ts = self._getTokenSet()
        assert len(ts) > 0, 'There must be a first token!'
        return ts[0]
        
    def _getTokenSet(self):
        """Return TokenSet for this node, cached in self._token_set."""
        if self._token_set:
            return self._token_set
        extent = self.node.extent
        translation_unit = self.node.translation_unit
        self._token_set = ci.tokenize(translation_unit, extent)
        return self._token_set

    # ------------------------------------------------------------------------
    # Level-Related Methods
    # ------------------------------------------------------------------------
        
    def _getLevelImpl(self):
        """Return suggested level for this handler, as suggested by the parent."""
        suggested_level = self.parent.suggestedChildLevel(self)
        return suggested_level

    def suggestedChildLevel(self, indent_syntax_node_handler):
        """Return suggested level for children."""
        if self.shouldIncreaseIndent():
            return IndentLevel(base=self.level, offset=self.config.indentation_size)
        else:
            return IndentLevel(base=self.level, offset=0)

    def logViolation(self, rule_type, node, text):
        """Log a rule violation with the given type, location, and text."""
        v = lv.RuleViolation(rule_type, node.extent.start.file.name, node.extent.start.line,
                             node.extent.start.column, text)
        self.violations.add(v)

    def shouldIncreaseIndent(self):
        """Returns true if children should have an increased indent level.

        Override this function to change the default behaviour of returning
        False.
        """
        return False

    # ------------------------------------------------------------------------
    # Indentation Checking-Related
    # ------------------------------------------------------------------------
    
    def checkIndentation(self):
        raise Exception('Abstract method!')

    # ------------------------------------------------------------------------
    # Method For Checking Cursor/Token Positions
    # ------------------------------------------------------------------------

    def startsLine(self, node):
        """Check whether the given node is at the beginning of the line."""
        return self.getLineStart(node) == self.expanddTabsColumnNo(node)

    def areOnSameLine(self, node1, node2):
        """Check whether two nodes start on the same line."""
        return node1 and node2 and node1.extent.start.line == node2.extent.start.line

    def areOnSameColumn(self, node1, node2):
        """Check whether two nodes start on the same column."""
        return node1 and node2 and node1.extent.start.column == node2.extent.start.column

    def areAdjacent(self, node1, node2):
        """Check whether two nodes are directly adjacent."""
        if node1.location.file.name != node2.location.file.name:
            return False
        if node1.extent.end.line != node2.extent.start.line:
            return False
        if node1.extent.end.column != node2.extent.start.column:
            return False
        return True

    def expandedTabsColumnNo(self, node):
        """Return column of node after expanding tabs."""
        npath, contents, lines = self.indentation_check.file_reader.readFile(node.extent.start.file.name)
        line = lines[node.extent.start.line - 1]
        return lengthExpandedTabs(line, node.extent.start.column - 1, self.indentation_check.config.tab_size)


class RootHandler(IndentSyntaxNodeHandler):
    """Handler registered at the root of the cursor hierarchy."""

    def __init__(self, indentation_check):
        super(RootHandler, self).__init__(indentation_check, None, None, None)
    
    def checkIndentation(self):
        pass  # Nothing to check.

    def _getLevelImpl(self):
        return IndentLevel(indent=0)


class CurlyBraceBlockHandler(IndentSyntaxNodeHandler):
    """Handler for curly brace blocks."""
    
    def checkCurlyBraces(self, indent_type):
        """Check curly braces of the block.

        @param indent_type  The indent type for the braces, one of 'same-line',
                            'next-line', and 'next-line-indent'.
        """
        lbrace = self.getLCurlyBrace()
        rbrace = self.getRCurlyBrace()
        t = self.getTokenLeftOfLeftLCurlyBrace()
        # Exit if there is no left curly brace and check for coherence of rbrace
        # and t.
        if lbrace is None:
            assert rbrace is None
            assert t is None
            return
        if indent_type == 'same-line':
            if not self.areOnSameLine(t, lbrace):
                msg = 'Opening brace should be on the same line as the token left of it.'
                self.logViolation('indent.brace', lbrace, msg)
            if not self.areOnSameColumn(self.getFirstToken(), rbrace):
                msg = 'Closing brace should be on the same column as block start.'
                self.logViolation('indent.brace', rbrace, msg)
        elif indent_type == 'next-line':
            if not self.areOnSameColumn(self.getFirstToken(), lbrace):
                msg = 'Opening brace should be on the same column as block start.'
                self.logViolation('indent.brace', lbrace, msg)
            if t.extent.start.line == lbrace.extent.start.line + 1:
                msg = 'Opening brace should be on the line directly after block start.'
                self.logViolation('indent.brace', lbrace, msg)
            if not self.areOnSameColumn(self.getFirstToken(), rbrace):
                msg = 'Closing brace should be on the same column as block start.'
                self.logViolation('indent.brace', rbrace, msg)
        else:
            assert indent_type == 'next-line-indent'
            if t.extent.start.line == lbrace.extent.start.line + 1:
                msg = 'Opening brace should be on the line directly after block start.'
                self.logViolation('indent.brace', lbrace, msg)
            # Check that the opening and closing braces are indented one level
            # further than the block start.
            next_level = IndentLevel(base=self.level, offset=self.config.indentation_size)
            print 'rbrace     ', rbrace.spelling, rbrace.extent
            print 'next level ', next_level
            if not next_level.accept(self.expandedTabsColumnNo(lbrace)):
                msg = 'Opening brace should be indented one level further than block start.'
                self.logViolation('indent.brace', lbrace, msg)
            if not next_level.accept(self.expandedTabsColumnNo(rbrace)):
                msg = 'Closing brace should be indented one level further than block start.'
                self.logViolation('indent.brace', rbrace, msg)

    def getTokenLeftOfLeftLCurlyBrace(self):
        """Return the token left of the first opening curly brace or None."""
        tk = ci.TokenKind
        token_set = self._getTokenSet()
        res = None
        for t in token_set:
            if t.kind == tk.PUNCTUATION and t.spelling == '{':
                return res
            res = t
        return None
        
    def getLCurlyBrace(self):
        """"Return the first opening curly brace or None."""
        tk = ci.TokenKind
        token_set = self._getTokenSet()
        for t in token_set:
            if t.kind == tk.PUNCTUATION and t.spelling == '{':
                return t
        return None

    def getRCurlyBrace(self):
        """Return the last closing curly brace or None."""
        tk = ci.TokenKind
        token_set = self._getTokenSet()
        for t in reversed(token_set):
            if t.kind == tk.PUNCTUATION and t.spelling == '}':
                return t
        return None


# ============================================================================
# Handlers For AST Nodes
# ============================================================================


class AddrLabelExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ArraySubscriptExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class AsmStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class BinaryOperatorHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class BlockExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class BreakStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CallExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CaseStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CharacterLiteralHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ClassDeclHandler(CurlyBraceBlockHandler):
    """Handler for class declarations.

    This does not include class template declarations or partial class template
    specializations.
    """
    
    def checkIndentation(self):
        # TODO(holtgrew): Check position of first token.
        # Check position of braces.
        self.checkCurlyBraces(self.config.brace_positions_class_struct_declaration)

    def shouldIncreaseIndent(self):
        return self.config.indent_inside_class_struct_body


class ClassTemplateHandler(CurlyBraceBlockHandler):
    """Handler for class templates.

    This includes struct templates.
    """
    
    def checkIndentation(self):
        # TODO(holtgrew): Check position of first token.
        # Check position of braces.
        self.checkCurlyBraces(self.config.brace_positions_class_struct_declaration)

    def shouldIncreaseIndent(self):
        return self.config.indent_inside_class_struct_body


class ClassTemplatePartialSpecializationHandler(CurlyBraceBlockHandler):
    """Handler for partial class template specializations.

    This includes struct templates.
    """
    
    def checkIndentation(self):
        # TODO(holtgrew): Check position of first token.
        # Check position of braces.
        self.checkCurlyBraces(self.config.brace_positions_class_struct_declaration)

    def shouldIncreaseIndent(self):
        return self.config.indent_inside_class_struct_body


class CompoundAssignmentOperatorHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CompoundLiteralExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CompoundStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ConditonalOperatorHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ConstructorHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ContinueStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ConversionFunctionHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CstyleCastExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxAccessSpecDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.

    # TODO(holtgrew): Should be indented by one more level if self.config.indent_visibility_specifiers.
    # TODO(holtgrew): Adding indentation for next tokens in case of self.config.indent_below_visibility_specifiers.


class CxxBaseSpecifierHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxBoolLiteralExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxCatchStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxConstCastExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxDeleteExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxDynamicCastExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxForRangeStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxFunctionalCastExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxMethodHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxNewExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxNullPtrLiteralExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxReinterpretCastExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxStaticCastExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxThisExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxThrowExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxTryStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxTypeidExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class CxxUnaryExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class DeclRefExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class DeclStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class DefaultStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class DestructorHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class DoStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class EnumConstantDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class EnumDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.

    def shouldIncreaseIndent(self):
        return self.config.indent_inside_class_struct_body


class FieldDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class FloatingLiteralHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ForStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class FunctionDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class FunctionTemplateHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class GenericSelectionExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class GnuNullExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class GotoStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class IbActionAttrHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class IbOutletAttrHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class IbOutletCollectionAttrHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class IfStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ImaginaryLiteralHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class InclusionDirectiveHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class IndirectGotoStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class InitListExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class IntegerLiteralHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class InvalidCodeHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class InvalidFileHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class LabelRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class LabelStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class LinkageSpecHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class MacroDefinitionHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class MacroInstantiationHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class MemberRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class MemberRefExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class NamespaceHandler(CurlyBraceBlockHandler):
    def checkIndentation(self):
        # TODO(holtgrew): Check position of first token.
        # Check position of braces.
        self.checkCurlyBraces(self.config.brace_positions_namespace_declaration)

    def shouldIncreaseIndent(self):
        return self.config.indent_declarations_within_namespace_definition


class NamespaceAliasHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class NamespaceRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class NotImplementedHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class NoDeclFoundHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class NullStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcAtCatchStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcAtFinallyStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcAtSynchronizedStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcAtThrowStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcAtTryStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcAutoreleasePoolStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcBridgeCastExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcCategoryDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcCategoryImplDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcClassMethodDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcClassRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcDynamicDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcEncodeExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcForCollectionStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcImplementationDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcInstanceMethodDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcInterfaceDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcIvarDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcMessageExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcPropertyDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcProtocolDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcProtocolExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcProtocolRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcSelectorExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcStringLiteralHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcSuperClassRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ObjcSynthesizeDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class OverloadedDeclRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class PackExpansionExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ParenExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ParmDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class PreprocessingDirectiveHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class ReturnStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class SehExceptStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class SehFinallyStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class SehTryStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class SizeOfPackExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class StringLiteralHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class StructDeclHandler(ClassDeclHandler):
    """The handler for struct declarations is the same as for classes.

    Subclassing is (mis-)used as quasi-aliasing here.
    """


class SwitchStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.

    def shouldIncreaseIndent(self):
        return self.config.indent_within_switch_body


class StmtexprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class TemplateNonTypeParameterHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class TemplateRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class TemplateTemplateParameterHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class TemplateTypeParameterHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class TranslationUnitHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class TypedefDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        """Check indentation of typedef declaration."""
        # TODO(holtgrew): Currently, only checking for the indentation of the first token is implemented. Implement more!
        print 'CHECK INDENTATION', self.level, self.expandedTabsColumnNo(self.node)
        if not self.level.accept(self.expandedTabsColumnNo(self.node)):
            # This indentation level is not valid.
            self.logViolation('indent.statement', self.node,
                              'Invalid indentation level.')


class TypeAliasDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class TypeRefHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class UnaryOperatorHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class UnexposedAttrHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class UnexposedDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class UnexposedExprHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class UnexposedStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class UnionDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class UsingDeclarationHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class UsingDirectiveHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class VarDeclHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


class WhileStmtHandler(IndentSyntaxNodeHandler):
    def checkIndentation(self):
        pass  # Do nothing.


# ============================================================================
# Code For Indentation Check
# ============================================================================


def getHandler(indentation_check, node, parent):
    # Get node kind name as UPPER_CASE, get class name and class object.
    kind_name = repr(str(node.kind)).split('.')[-1]
    class_name = kind_name.replace('\'', '').replace('_', ' ').title().replace(' ', '') + 'Handler'
    klass = eval(class_name)
    # Instantiate handler and return.
    handler = klass(indentation_check, kind_name, node, parent)
    return handler


class UnknownParameter(Exception):
    """Raised when an unknown indentation parameter is used."""


class IndentationConfig(object):
    """Configuration for the indentation check.

    Look into the source for all the settings, there are a LOT.
    """
    
    def __init__(self, **kwargs):
        """Initialize indentation settings with K&R style."""

        # The settings here are based on the Eclipse CDT indentation config, K&R
        # style.
        #
        # We will first set the default values.  Then, this represents the known
        # style parameters and we will overwrite the properties from kwargs if
        # they are already there.
        
        # --------------------------------------------------------------------
        # General Settings
        # --------------------------------------------------------------------

        # The policy for tabs to accept.
        # Valid values: 'tabs-only', 'spaces-only', 'mixed'.
        self.tab_policy = 'spaces-only'
        # The number of spaces to use for one indentation.
        self.indentation_size = 4
        # The number of spaces that one TAB character is wide.
        self.tab_size = 4

        # --------------------------------------------------------------------
        # Indent
        # --------------------------------------------------------------------
        
        # Indent 'public', 'protected', and 'private' within class body.
        self.indent_visibility_specifiers = False
        # Indent declarations relative to 'public', 'procted, and 'private'.
        self.indent_below_visibility_specifiers = True
        # Indent declarations relative to class/struct body.
        self.indent_inside_class_struct_body = True
        # Indent statements within function bodies.
        self.indent_statements_within_function_bodies = True
        # Indent statements within blocks.
        self.indent_statements_within_blocks = True
        # Indent statements within switch blocks.
        self.indent_statements_within_switch_body = False
        # Indent statements within case body.
        self.indent_statements_within_case_body = True
        # Indent 'break' statements.
        self.indent_break_statements = True
        # Indent declarations within 'namespace' definition.
        self.indent_declarations_within_namespace_definition = False
        # Indent empty lines.  DEACTIVATED
        # self.indent_empty_lines = False

        # --------------------------------------------------------------------
        # Brace Positions
        # --------------------------------------------------------------------

        # Valid values for the following variables are 'same-line', 'next-line',
        # 'next-line-indented'.

        # Brace positions for class / struct declarations.
        self.brace_positions_class_struct_declaration = 'same-line'
        # Brace positions for namespace declarations.
        self.brace_positions_namespace_declaration = 'same-line'
        # Brace positions for function declarations.
        self.brace_positions_function_declaration = 'same-line'
        # Brace positions for blocks.
        self.brace_positions_blocks = 'same-line'
        # Brace positions of blocks in case statements.
        self.brace_positions_blocks_in_case_statement = 'same-line'
        # Brace positions of switch statements.
        self.brace_positions_switch_statement = 'same-line'
        # Brace positions for initializer list.
        self.brace_positions_brace_positions_initializer_list = 'same-line'
        # Keep empty initializer list on one line.
        self.brace_positions_keep_empty_initializer_list_on_one_line = True

        # --------------------------------------------------------------------
        # White Space
        # --------------------------------------------------------------------

        # Declarations / Types
        
        # Insert space before opening brace of a class.
        self.insert_space_before_opening_brace_of_a_class = True
        # Insert space before colon of base clause.
        self.insert_space_before_colon_of_base_clause = False
        # Insert space after colon of base clause.
        self.insert_space_after_colon_of_base_clause = True
        # Insert space before_comma_in_base_clause.
        self.insert_space_before_comma_in_base_clause = False
        # Insert space after comma in base clause.
        self.insert_space_after_comma_in_base_clause = True

        # Declarations / Declarator list

        # Insert space before comma in declarator list.
        self.insert_space_before_comma_in_declarator_list = False
        # Insert space after comma in declarator list.
        self.insert_space_after_comma_in_declarator_list = True

        # Declarations / Functions

        self.insert_space_before_opening_function_parenthesis = False
        self.insert_space_after_opening_function_parenthesis = False
        self.insert_space_before_closing_function_parenthesis = False
        self.insert_space_between_empty_function_parentheses = False
        self.insert_space_before_opening_function_brace = True
        self.insert_space_before_comma_in_parameters = False
        self.insert_space_after_comma_in_parameters = True

        # Declarations / Exception Specification

        self.insert_space_before_opening_exception_specification_parenthesis = True
        self.insert_space_after_opening_exception_specification_parenthesis = False
        self.insert_space_before_closing_exception_specification_parenthesis = False
        self.insert_space_between_empty_exception_specification_parenthesis = True
        self.insert_space_before_comma_in_exception_specification_parameters = False
        self.insert_space_after_comma_in_exception_specification_parameters = True

        # Declarations / Labels

        self.insert_space_before_label_colon = False
        self.insert_space_after_label_colon = True

        # Control Statements

        self.insert_space_before_control_statement_semicolon = False;

        # Control Statements / Blocks

        self.insert_space_before_opening_block_brace = True
        self.insert_space_after_closing_block_brace = True

        # Control Statements / 'if else'

        self.insert_space_before_opening_if_else_parenthesis = True
        self.insert_space_after_opening_if_else_parenthesis = False
        self.insert_space_before_closing_if_else_parenthesis = False

        # Control Statements / 'for'

        self.insert_space_before_opening_for_parenthesis = True
        self.insert_space_after_opening_for_parenthesis = False
        self.insert_space_before_closing_for_parenthesis = False
        self.insert_space_before_for_semicolon = False
        self.insert_space_after_for_semicolon = True

        # Control Statements / 'switch'

        self.insert_space_before_colon_in_switch_case = False
        self.insert_space_before_colon_in_switch_default = False
        self.insert_space_before_opening_switch_brace = True
        self.insert_space_before_opening_switch_parenthesis = True
        self.insert_space_after_opening_switch_parenthesis = False
        self.insert_space_before_closing_switch_parenthesis = False

        # Control Statements / 'while' & 'do while'

        self.insert_space_before_opening_do_while_parenthesis = True
        self.insert_space_after_opening_do_while_parenthesis = False
        self.insert_space_before_closing_do_while_parenthesis = False

        # Control Statements / 'catch'

        self.insert_space_before_opening_catch_parenthesis = True
        self.insert_space_after_opening_catch_parenthesis = False
        self.insert_space_before_closing_catch_parenthesis = False

        # Expressions / Function invocations

        self.insert_space_before_opening_function_invocation_parenthesis = False
        self.insert_space_after_opening_function_invocation_parenthesis = False
        self.insert_space_before_closing_function_invocation_parenthesis = False
        self.insert_space_between_empty_function_invocation_parentheses = False
        self.insert_space_before_comma_in_function_arguments = False
        self.insert_space_after_comma_in_function_arguments = True

        # Expressions / Assignments

        self.insert_space_before_assignment_operator = True
        self.insert_space_after_assignment_operator = True

        # Expressions / Initializer list

        self.insert_space_before_opening_initializer_list_brace = True
        self.insert_space_after_opening_initializer_list_brace = True
        self.insert_space_before_closing_initializer_list_brace = True
        self.insert_space_before_initializer_list_comma = False
        self.insert_space_after_initializer_list_comma = True
        self.insert_space_between_empty_initializer_list_braces = False

        # Expressions / Operators

        self.insert_space_before_binary_operators = True
        self.insert_space_after_binary_operators = True
        self.insert_space_before_unary_operators = False
        self.insert_space_after_unary_operators = False
        self.insert_space_before_prefix_operators = False
        self.insert_space_after_prefix_operators = False
        self.insert_space_before_postfix_operators = False
        self.insert_space_after_postfix_operators = False

        # Expressions / Parenthesized expressions

        self.insert_space_before_opening_parenthesis = False
        self.insert_space_after_opening_parenthesis = False
        self.insert_space_before_closing_parenthesis = False

        # Expressions / Type casts

        self.insert_space_after_opening_parenthesis = False
        self.insert_space_before_closing_parenthesis = False
        self.insert_space_after_closing_parenthesis = True

        # Expressions / Conditionals

        self.insert_space_before_conditional_question_mark = True
        self.insert_space_after_conditional_question_mark = True
        self.insert_space_before_conditional_colon = True
        self.insert_space_after_conditional_colon = True

        # Expressions / Expression list

        self.insert_space_before_comma_in_expression_list = False
        self.insert_space_after_comma_in_expression_list = True

        # Arrays

        self.insert_space_before_opening_array_bracket = False
        self.insert_space_after_opening_array_bracket = False
        self.insert_space_before_closing_array_bracket = False
        self.insert_space_between_empty_array_brackets = False

        # Templates / Template arguments

        self.insert_space_before_opening_template_argument_angle_bracket = False
        self.insert_space_after_opening_template_argument_angle_bracket = False
        self.insert_space_before_template_argument_comma = False
        self.insert_space_after_template_argument_comma = True
        self.insert_space_before_closing_template_argument_angle_bracket = False
        self.insert_space_after_closing_template_argument_angle_bracket = True

        # Templates / Template parameters

        self.insert_space_before_opening_template_parameter_angle_bracket = False
        self.insert_space_after_opening_template_parameter_angle_bracket = False
        self.insert_space_before_template_parameter_comma = False
        self.insert_space_after_template_parameter_comma = True
        self.insert_space_before_closing_template_parameter_angle_bracket = False
        self.insert_space_after_closing_template_parameter_angle_bracket = True
        

        # --------------------------------------------------------------------
        # Control Statements (General)
        # --------------------------------------------------------------------

        # Insert new line before 'else' in an 'if' statement.
        self.insert_new_line_before_else_in_an_if_statement = False
        # Insert new line before 'catch' in a 'try' statement.
        self.insert_new_line_before_catch_in_a_try_statement = False
        # Insert new line before 'while' in a 'do' statement.
        self.insert_new_line_before_while_in_a_do_statement = False

        # --------------------------------------------------------------------
        # Control Statements ('if else')
        # --------------------------------------------------------------------

        # Keep 'then' statement on same line.
        self.keep_then_statement_on_same_line = False
        # Keep simple 'if' on one line.
        self.keep_simple_if_on_one_line = False
        # Keep 'else' statement on same line.
        self.keep_else_statement_on_same_line = False
        # Keep 'else if' on one line.
        self.keep_else_if_on_one_line = True

        # --------------------------------------------------------------------
        # Line Wrapping
        # --------------------------------------------------------------------

        # Line wrappings in brackets (parameter and initializer lists).

        # Wrapped lines are allowed to flush to the start of the previous list.
        self.line_wrapping_allow_flush = True
        # Wrapped lines are allowed to be indented.
        self.line_wrapping_allow_indent = True
        # By how many levels to indent wrapped lines.
        self.line_wrapping_indent = 2

        # Wrapped initializer list lines are allowed to flush to the start of
        # the previous list.
        self.line_wrapping_initializer_list_allow_flush = True
        # Wrapped initializer list lines are allowed to be indented.
        self.line_wrapping_initializer_list_allow_indent = True
        # By how many levels to indent wrapped initializer list lines.
        self.line_wrapping_initializer_list_indent = 2

        # --------------------------------------------------------------------
        # Overwrite From kwargs
        # --------------------------------------------------------------------

        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise UnknownParameter('Unknown parameter "%s".' % key)
            setattr(self, key, value)
        

class IndentationCheck(lc.TreeCheck):
    """Check for code and brace indentation."""

    def __init__(self, config=IndentationConfig()):
        super(IndentationCheck, self).__init__()
        self.config = config
        self.handlers = []
        self.level = 0

    def beginTree(self, node):
        logging.debug('IndentationCheck: BEGIN TREE(%s)', node)
        assert len(self.handlers) == 0
        self.handlers = [RootHandler(self)]

    def endTree(self, node):
        logging.debug('IndentationCheck: END TREE(%s)', node)
        assert len(self.handlers) == 1
        self.handlers = []

    def enterNode(self, node):
        logging.debug('%sEntering Node: %s %s (%s)', '  ' * self.level, node.kind, node.spelling, node.location)
        handler = getHandler(self, node, self.handlers[-1])
        logging.debug('  %s[indent level=%s]', '  ' * self.level, str(handler.level))
        self.handlers.append(handler)
        if handler:
            handler.checkIndentation()
        self.level += 1

    def exitNode(self, node):
        self.level -= 1
        self.handlers.pop()
