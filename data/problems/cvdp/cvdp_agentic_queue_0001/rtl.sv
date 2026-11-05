module queue #(
    parameter int DEPTH = 4, parameter int DBITS = 8,
    parameter int ALMOST_EMPTY_THRESHOLD = 1,
    parameter int ALMOST_FULL_THRESHOLD = 1
) (
    input logic clk_i, rst_ni, clr_i, ena_i, we_i, re_i,
    input logic [DBITS-1:0] d_i,
    output logic [DBITS-1:0] q_o,
    output logic empty_o, full_o, almost_empty_o, almost_full_o
);
    localparam int CW=(DEPTH<2) ? 1 : $clog2(DEPTH+1);
    localparam int AE=(ALMOST_EMPTY_THRESHOLD>DEPTH) ? DEPTH : ALMOST_EMPTY_THRESHOLD;
    localparam int AF=(ALMOST_FULL_THRESHOLD>DEPTH) ? 0 : DEPTH-ALMOST_FULL_THRESHOLD;
    logic [DBITS-1:0] data_q [0:DEPTH-1];
    logic [CW-1:0] count;
    wire remove_head = re_i && count!=0;
    wire append_tail = we_i && (count<DEPTH || remove_head);
    wire [CW-1:0] after_read = count-CW'(remove_head);
    assign q_o=count==0 ? DBITS'(0) : data_q[0];
    assign empty_o=count==0;
    assign full_o=count==DEPTH;
    assign almost_empty_o=count<=AE;
    assign almost_full_o=count>=AF;
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            count<=0;
            for (int i=0;i<DEPTH;i++) data_q[i]<=0;
        end else if (clr_i) begin
            count<=0;
            for (int i=0;i<DEPTH;i++) data_q[i]<=0;
        end else if (ena_i) begin
            if (remove_head) begin
                for (int i=0;i<DEPTH-1;i++) data_q[i]<=data_q[i+1];
                data_q[DEPTH-1]<=0;
            end
            if (append_tail) data_q[after_read]<=d_i;
            count<=after_read+CW'(append_tail);
        end
    end
endmodule
