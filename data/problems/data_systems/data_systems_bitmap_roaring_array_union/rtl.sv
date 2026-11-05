module data_systems_bitmap_roaring_array_union (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [5:0] in_card_1,
    input logic [5:0] in_card_2,
    input logic [1023:0] in_array_1,
    input logic [1023:0] in_array_2,
    output logic out_valid,
    input logic out_ready,
    output logic [6:0] out_card,
    output logic [2047:0] out_array,
    output logic out_error
);
    logic busy, invalid_input;
    logic [1023:0] a, b;
    logic [6:0] i, j, n, m;
    logic [15:0] av, bv;

    assign in_ready = !rst && !busy && (!out_valid || out_ready);
    assign av = a[16*i +: 16];
    assign bv = b[16*j +: 16];
    always_comb begin
        invalid_input = 1'b0;
        for (integer k=1; k<63; k=k+1) begin
            if (k < in_card_1 && in_array_1[16*k +: 16] <= in_array_1[16*(k-1) +: 16])
                invalid_input = 1'b1;
            if (k < in_card_2 && in_array_2[16*k +: 16] <= in_array_2[16*(k-1) +: 16])
                invalid_input = 1'b1;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_valid <= 0;
            out_card <= 0;
            out_array <= 0;
            out_error <= 0;
            a <= 0; b <= 0;
            i <= 0; j <= 0; n <= 0; m <= 0;
        end else begin
            if (out_valid && out_ready) out_valid <= 0;
            if (in_valid && in_ready) begin
                a <= in_array_1;
                b <= in_array_2;
                n <= {1'b0,in_card_1};
                m <= {1'b0,in_card_2};
                i <= 0; j <= 0;
                out_card <= 0;
                out_array <= 0;
                out_error <= invalid_input;
                busy <= !invalid_input;
                out_valid <= invalid_input;
            end else if (busy) begin
                if (i == n && j == m) begin
                    busy <= 0;
                    out_valid <= 1;
                end else begin
                    if (i < n && (j == m || av < bv)) begin
                        out_array[16*out_card +: 16] <= av;
                        i <= i + 1'b1;
                    end else if (j < m && (i == n || bv < av)) begin
                        out_array[16*out_card +: 16] <= bv;
                        j <= j + 1'b1;
                    end else begin
                        out_array[16*out_card +: 16] <= av;
                        i <= i + 1'b1;
                        j <= j + 1'b1;
                    end
                    out_card <= out_card + 1'b1;
                end
            end
        end
    end
endmodule
