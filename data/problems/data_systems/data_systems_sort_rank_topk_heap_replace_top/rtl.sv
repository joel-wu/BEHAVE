module data_systems_sort_rank_topk_heap_replace_top #(
    parameter int unsigned K = 8,
    parameter int unsigned VALUE_W = 32,
    parameter int unsigned ID_W = 64
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic [K*VALUE_W-1:0]         in_bh_val,
    input  logic [K*ID_W-1:0]            in_bh_ids,
    input  logic [VALUE_W-1:0]           in_val,
    input  logic [ID_W-1:0]              in_id,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [K*VALUE_W-1:0]         out_bh_val,
    output logic [K*ID_W-1:0]            out_bh_ids
);

    logic busy;
    logic [K*VALUE_W-1:0] out_bh_val_next;
    logic [K*ID_W-1:0]    out_bh_ids_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic cmp2(
        input logic [VALUE_W-1:0] a_val,
        input logic [VALUE_W-1:0] b_val,
        input logic [ID_W-1:0]    a_id,
        input logic [ID_W-1:0]    b_id
    );
        begin
            if (a_val > b_val) begin
                cmp2 = 1'b1;
            end else if (a_val == b_val) begin
                cmp2 = (a_id > b_id);
            end else begin
                cmp2 = 1'b0;
            end
        end
    endfunction

    integer i;
    integer idx;
    integer i1;
    integer i2;
    integer c;
    integer level;
    logic searching;
    logic [VALUE_W-1:0] bh_val [0:K-1];
    logic [ID_W-1:0]    bh_ids [0:K-1];
    logic [VALUE_W-1:0] cur_val;
    logic [ID_W-1:0]    cur_id;
    logic [K*VALUE_W-1:0] packed_val;
    logic [K*ID_W-1:0]    packed_ids;

    always_comb begin
        for (i = 0; i < K; i = i + 1) begin
            bh_val[i] = in_bh_val[i*VALUE_W +: VALUE_W];
            bh_ids[i] = in_bh_ids[i*ID_W +: ID_W];
        end

        cur_val = in_val;
        cur_id  = in_id;
        idx     = 0;
        i1      = 0;
        i2      = 0;
        c       = 0;
        searching = 1'b1;

        // Each move descends one level. This includes a leaf check for K=1
        // and all paths in a K-element binary tree; no data-dependent unroll.
        for (level = 0; level <= $clog2(K); level = level + 1) begin
            if (searching) begin
                i1 = (idx << 1) + 1;
                i2 = i1 + 1;
                if (i1 >= K) begin
                    searching = 1'b0;
                end else begin
                    if (i2 >= K) begin
                        c = i1;
                    end else if (cmp2(bh_val[i1], bh_val[i2], bh_ids[i1], bh_ids[i2])) begin
                        c = i1;
                    end else begin
                        c = i2;
                    end
                    if (cmp2(cur_val, bh_val[c], cur_id, bh_ids[c])) begin
                        searching = 1'b0;
                    end else begin
                        bh_val[idx] = bh_val[c];
                        bh_ids[idx] = bh_ids[c];
                        idx = c;
                    end
                end
            end
        end

        bh_val[idx] = cur_val;
        bh_ids[idx] = cur_id;

        packed_val = '0;
        packed_ids = '0;
        for (i = 0; i < K; i = i + 1) begin
            packed_val[i*VALUE_W +: VALUE_W] = bh_val[i];
            packed_ids[i*ID_W +: ID_W] = bh_ids[i];
        end

        out_bh_val_next = packed_val;
        out_bh_ids_next = packed_ids;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_bh_val <= '0;
            out_bh_ids <= '0;
        end else if (input_accepted) begin
            out_bh_val <= out_bh_val_next;
            out_bh_ids <= out_bh_ids_next;
            busy       <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
