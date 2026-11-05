module cvdp_copilot_decode_firstbit #(
    parameter int InWidth_g = 32,
    parameter int InReg_g = 1,
    parameter int OutReg_g = 1,
    parameter int PlRegs_g = 1
) (
    input logic Clk, Rst,
    input logic [InWidth_g-1:0] In_Data,
    input logic In_Valid,
    output logic [(InWidth_g>1 ? $clog2(InWidth_g) : 1)-1:0] Out_FirstBit,
    output logic Out_Found, Out_Valid
);
    localparam int LEVELS=$clog2(InWidth_g);
    localparam int IW=(LEVELS>0 ? LEVELS : 1);
    localparam int PAD=2**LEVELS;
    logic [InWidth_g-1:0] data_q;
    logic input_valid;
    if (InReg_g) begin : input_register
        always_ff @(posedge Clk or posedge Rst)
            if (Rst) begin data_q<=0; input_valid<=0; end
            else begin data_q<=In_Data; input_valid<=In_Valid; end
    end else begin : input_bypass
        assign data_q=In_Data;
        assign input_valid=In_Valid;
    end
    // A level contains only its active nodes. Each node prefers its lower half.
    for (genvar level=0;level<=LEVELS;level++) begin : tree
        localparam int NODES=PAD>>level;
        logic [NODES-1:0] found;
        logic [NODES*IW-1:0] indices;
        logic valid;
        if (level==0) begin : leaves
            assign valid=input_valid;
            for (genvar j=0;j<NODES;j++) begin : leaf
                if (j<InWidth_g) assign found[j]=data_q[j];
                else assign found[j]=1'b0;
                assign indices[j*IW +: IW]=IW'(j);
            end
        end else begin : reduction
            localparam bit REGISTERED=((level*PlRegs_g)/LEVELS != ((level-1)*PlRegs_g)/LEVELS);
            wire [NODES-1:0] found_next;
            wire [NODES*IW-1:0] indices_next;
            for (genvar j=0;j<NODES;j++) begin : node
                assign found_next[j]=tree[level-1].found[2*j] | tree[level-1].found[2*j+1];
                assign indices_next[j*IW +: IW]=tree[level-1].found[2*j] ?
                    tree[level-1].indices[2*j*IW +: IW] : tree[level-1].found[2*j+1] ?
                    tree[level-1].indices[(2*j+1)*IW +: IW] : IW'(0);
            end
            if (REGISTERED) begin : registers
                always_ff @(posedge Clk or posedge Rst)
                    if (Rst) begin found<=0; indices<=0; valid<=0; end
                    else begin found<=found_next; indices<=indices_next; valid<=tree[level-1].valid; end
            end else begin
                assign found=found_next;
                assign indices=indices_next;
                assign valid=tree[level-1].valid;
            end
        end
    end
    wire [IW-1:0] final_index=tree[LEVELS].found[0] ? tree[LEVELS].indices : IW'(0);
    if (OutReg_g) begin : output_register
        always_ff @(posedge Clk or posedge Rst)
            if (Rst) begin Out_FirstBit<=0; Out_Found<=0; Out_Valid<=0; end
            else begin
                Out_FirstBit<=final_index;
                Out_Found<=tree[LEVELS].found[0]; Out_Valid<=tree[LEVELS].valid;
            end
    end else begin : output_bypass
        assign Out_FirstBit=Rst ? IW'(0) : final_index;
        assign Out_Found=!Rst && tree[LEVELS].found[0];
        assign Out_Valid=!Rst && tree[LEVELS].valid;
    end
endmodule
