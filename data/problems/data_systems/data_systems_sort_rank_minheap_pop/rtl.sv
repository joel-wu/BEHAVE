module data_systems_sort_rank_minheap_pop (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [3:0]   in_k,
    input  logic [31:0]  in_val_0,
    input  logic [31:0]  in_val_1,
    input  logic [31:0]  in_val_2,
    input  logic [31:0]  in_val_3,
    input  logic [31:0]  in_val_4,
    input  logic [31:0]  in_val_5,
    input  logic [31:0]  in_val_6,
    input  logic [31:0]  in_val_7,
    input  logic [63:0]  in_id_0,
    input  logic [63:0]  in_id_1,
    input  logic [63:0]  in_id_2,
    input  logic [63:0]  in_id_3,
    input  logic [63:0]  in_id_4,
    input  logic [63:0]  in_id_5,
    input  logic [63:0]  in_id_6,
    input  logic [63:0]  in_id_7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_error,
    output logic [31:0]  out_val_0,
    output logic [31:0]  out_val_1,
    output logic [31:0]  out_val_2,
    output logic [31:0]  out_val_3,
    output logic [31:0]  out_val_4,
    output logic [31:0]  out_val_5,
    output logic [31:0]  out_val_6,
    output logic [31:0]  out_val_7,
    output logic [63:0]  out_id_0,
    output logic [63:0]  out_id_1,
    output logic [63:0]  out_id_2,
    output logic [63:0]  out_id_3,
    output logic [63:0]  out_id_4,
    output logic [63:0]  out_id_5,
    output logic [63:0]  out_id_6,
    output logic [63:0]  out_id_7
);
    logic busy;
    logic [31:0] vals [0:7];
    logic [63:0] ids [0:7];
    logic error, active;
    logic [31:0] last_value;
    logic [63:0] last_id;
    integer k, pos, left, right, child, parent, i, depth;
    assign in_ready = !busy || out_ready;
    assign out_valid = busy;
    function automatic logic less_pair(input logic [31:0] av, bv,
                                       input logic [63:0] ai, bi);
        less_pair = av < bv || (av == bv && ai < bi);
    endfunction
    always_comb begin
        vals[0] = in_val_0; ids[0] = in_id_0;
        vals[1] = in_val_1; ids[1] = in_id_1;
        vals[2] = in_val_2; ids[2] = in_id_2;
        vals[3] = in_val_3; ids[3] = in_id_3;
        vals[4] = in_val_4; ids[4] = in_id_4;
        vals[5] = in_val_5; ids[5] = in_id_5;
        vals[6] = in_val_6; ids[6] = in_id_6;
        vals[7] = in_val_7; ids[7] = in_id_7;
        k = {28'd0, in_k};
        error = k < 1 || k > 8;
        parent = 0;
        for (i = 1; i < 8; i = i + 1) begin
            parent = (i-1)/2;
            if (i < k && less_pair(vals[i], vals[parent], ids[i], ids[parent]))
                error = 1;
        end
        pos = 1; left = 0; right = 0; child = 0;
        last_value = 0; last_id = 0; active = 0;
        if (!error) begin
            last_value = vals[k-1];
            last_id = ids[k-1];
            active = 1;
            for (depth = 0; depth < 4; depth = depth + 1) begin
                left = 2*pos;
                right = left+1;
                if (active && left <= k) begin
                    child = left;
                    if (right <= k)
                        if (!less_pair(vals[left-1], vals[right-1], ids[left-1], ids[right-1]))
                            child = right;
                    if (less_pair(last_value, vals[child-1], last_id, ids[child-1]))
                        active = 0;
                    else begin
                        vals[pos-1] = vals[child-1];
                        ids[pos-1] = ids[child-1];
                        pos = child;
                    end
                end else active = 0;
            end
            vals[pos-1] = last_value;
            ids[pos-1] = last_id;
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_error <= 0;
            out_val_0 <= 0; out_id_0 <= 0;
            out_val_1 <= 0; out_id_1 <= 0;
            out_val_2 <= 0; out_id_2 <= 0;
            out_val_3 <= 0; out_id_3 <= 0;
            out_val_4 <= 0; out_id_4 <= 0;
            out_val_5 <= 0; out_id_5 <= 0;
            out_val_6 <= 0; out_id_6 <= 0;
            out_val_7 <= 0; out_id_7 <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            out_error <= error;
            out_val_0 <= vals[0]; out_id_0 <= ids[0];
            out_val_1 <= vals[1]; out_id_1 <= ids[1];
            out_val_2 <= vals[2]; out_id_2 <= ids[2];
            out_val_3 <= vals[3]; out_id_3 <= ids[3];
            out_val_4 <= vals[4]; out_id_4 <= ids[4];
            out_val_5 <= vals[5]; out_id_5 <= ids[5];
            out_val_6 <= vals[6]; out_id_6 <= ids[6];
            out_val_7 <= vals[7]; out_id_7 <= ids[7];
        end else if (out_ready) busy <= 0;
    end
endmodule
