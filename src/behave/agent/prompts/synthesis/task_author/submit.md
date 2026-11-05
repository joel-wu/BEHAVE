Finish with draft containing the required task files and scope_findings=[]. This
submits a proposal, not an approved task. To request a change of scope instead,
submit draft=null and a nonempty scope_findings list explaining the problem.

spec, contract and run_policy are workspace-relative file paths, not inline
content. The spec must use the exact section layout given in the instructions;
keep all model calling instructions in the Behavior interface subsection.
If submission reports a layout error, fix the named spec file and resubmit.
This structural check does not establish functional correctness.
contract contains a complete TaskContract JSON object. For an ordinary
task, the run_policy file contains JSON null to use runtime generation settings.
Read .construction/task_contract.md for the formats. behavior identifies the
existing B1 source files and their entry file/symbol. Set rtl=null; reference
RTL and a deliverable testbench are not required from Builder.

draft.adaptations replaces the inherited list; null preserves it. Put every
answer-determining choice in the public specification as well. public_files are
only supporting files intended for solvers, not B1, other private reference
implementations, construction notes or experience.
