module data_systems_sort_rank_merge_sort_step_int32 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_x0,
    input logic [31:0] in_x1,
    input logic [31:0] in_x2,
    input logic [31:0] in_x3,
    input logic [31:0] in_x4,
    input logic [31:0] in_x5,
    input logic [31:0] in_x6,
    input logic [31:0] in_x7,
    output logic out_valid,
    input logic out_ready,
    output logic [2:0] out_p0,
    output logic [2:0] out_p1,
    output logic [2:0] out_p2,
    output logic [2:0] out_p3,
    output logic [2:0] out_p4,
    output logic [2:0] out_p5,
    output logic [2:0] out_p6,
    output logic [2:0] out_p7
);
    logic [31:0] words [0:7];
    logic [32:0] keys [0:7];
    logic [2:0] order [0:7];
    logic [2:0] saved;
    logic busy;

    function automatic [32:0] sort_key(input logic [31:0] word);
        if (word[30:0] > 31'h7f800000)
            sort_key = 33'h100000000;
        else if (word[30:0] == 0)
            sort_key = 33'h080000000;
        else if (word[31])
            sort_key = {1'b0, ~word};
        else
            sort_key = {1'b0, word ^ 32'h80000000};
    endfunction

    assign words[0] = in_x0;
    assign words[1] = in_x1;
    assign words[2] = in_x2;
    assign words[3] = in_x3;
    assign words[4] = in_x4;
    assign words[5] = in_x5;
    assign words[6] = in_x6;
    assign words[7] = in_x7;

    assign in_ready = !busy || out_ready;
    assign out_valid = busy;

    always_comb begin
        saved = 0;
        for (int i = 0; i < 8; i++) begin
            keys[i] = sort_key(words[i]);
            order[i] = 3'(i);
        end
        for (int i = 1; i < 8; i++) begin
            for (int j = 7; j > 0; j--) begin
                if (j <= i && keys[order[j]] < keys[order[j-1]]) begin
                    saved = order[j-1];
                    order[j-1] = order[j];
                    order[j] = saved;
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_p0 <= 0;
            out_p1 <= 0;
            out_p2 <= 0;
            out_p3 <= 0;
            out_p4 <= 0;
            out_p5 <= 0;
            out_p6 <= 0;
            out_p7 <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            out_p0 <= order[0];
            out_p1 <= order[1];
            out_p2 <= order[2];
            out_p3 <= order[3];
            out_p4 <= order[4];
            out_p5 <= order[5];
            out_p6 <= order[6];
            out_p7 <= order[7];
        end else if (out_ready) begin
            busy <= 0;
        end
    end
endmodule
