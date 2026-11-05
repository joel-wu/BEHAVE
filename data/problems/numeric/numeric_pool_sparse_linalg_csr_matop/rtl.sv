module csr_matrix_add_row_merge (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_row,
    input  logic [31:0]  in_alpha,
    input  logic [31:0]  in_beta,

    input  logic [2:0]   in_a_row_start,
    input  logic [2:0]   in_a_row_end,
    input  logic [15:0]  in_a_col0,
    input  logic [15:0]  in_a_col1,
    input  logic [15:0]  in_a_col2,
    input  logic [15:0]  in_a_col3,
    input  logic [31:0]  in_a_val0,
    input  logic [31:0]  in_a_val1,
    input  logic [31:0]  in_a_val2,
    input  logic [31:0]  in_a_val3,

    input  logic [2:0]   in_b_row_start,
    input  logic [2:0]   in_b_row_end,
    input  logic [15:0]  in_b_col0,
    input  logic [15:0]  in_b_col1,
    input  logic [15:0]  in_b_col2,
    input  logic [15:0]  in_b_col3,
    input  logic [31:0]  in_b_val0,
    input  logic [31:0]  in_b_val1,
    input  logic [31:0]  in_b_val2,
    input  logic [31:0]  in_b_val3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_row,
    output logic [3:0]   out_nnz,
    output logic [15:0]  out_col0,
    output logic [15:0]  out_col1,
    output logic [15:0]  out_col2,
    output logic [15:0]  out_col3,
    output logic [31:0]  out_val0,
    output logic [31:0]  out_val1,
    output logic [31:0]  out_val2,
    output logic [31:0]  out_val3,
    output logic [15:0]  out_col4,
    output logic [15:0]  out_col5,
    output logic [15:0]  out_col6,
    output logic [15:0]  out_col7,
    output logic [31:0]  out_val4,
    output logic [31:0]  out_val5,
    output logic [31:0]  out_val6,
    output logic [31:0]  out_val7
);
    logic busy;
    logic [15:0] cols [0:7], result_cols [0:7];
    logic [31:0] vals [0:7], result_vals [0:7];
    integer count, position;
    logic active;
    logic [31:0] term;
    assign in_ready = !busy || out_ready;
    assign out_valid = busy;
    always_comb begin
        cols[0] = in_a_col0; vals[0] = in_a_val0;
        cols[1] = in_a_col1; vals[1] = in_a_val1;
        cols[2] = in_a_col2; vals[2] = in_a_val2;
        cols[3] = in_a_col3; vals[3] = in_a_val3;
        cols[4] = in_b_col0; vals[4] = in_b_val0;
        cols[5] = in_b_col1; vals[5] = in_b_val1;
        cols[6] = in_b_col2; vals[6] = in_b_val2;
        cols[7] = in_b_col3; vals[7] = in_b_val3;

        count=0; position=0; active=0; term=0;
        for (int j=0;j<8;j++) begin result_cols[j]=0; result_vals[j]=0; end
        for (int k=0;k<8;k++) begin
            active = k<4 ? (in_a_row_start<=k && k<in_a_row_end)
                         : (in_b_row_start<=k-4 && k-4<in_b_row_end);
            if (active) begin
                term = (k<4 ? in_alpha : in_beta) * vals[k];
                position=count;
                for (int j=0;j<8;j++)
                    if (j<count && result_cols[j]==cols[k]) position=j;
                if (position==count) begin result_cols[count]=cols[k]; count=count+1; end
                result_vals[position]=result_vals[position]+term;
            end
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            busy<=0; out_row<=0; out_nnz<=0;
            out_col0<=0; out_val0<=0;
            out_col1<=0; out_val1<=0;
            out_col2<=0; out_val2<=0;
            out_col3<=0; out_val3<=0;
            out_col4<=0; out_val4<=0;
            out_col5<=0; out_val5<=0;
            out_col6<=0; out_val6<=0;
            out_col7<=0; out_val7<=0;

        end else if (in_valid && in_ready) begin
            busy<=1; out_row<=in_row; out_nnz<=4'(count);
            out_col0<=result_cols[0]; out_val0<=result_vals[0];
            out_col1<=result_cols[1]; out_val1<=result_vals[1];
            out_col2<=result_cols[2]; out_val2<=result_vals[2];
            out_col3<=result_cols[3]; out_val3<=result_vals[3];
            out_col4<=result_cols[4]; out_val4<=result_vals[4];
            out_col5<=result_cols[5]; out_val5<=result_vals[5];
            out_col6<=result_cols[6]; out_val6<=result_vals[6];
            out_col7<=result_cols[7]; out_val7<=result_vals[7];

        end else if (out_valid && out_ready) busy<=0;
    end
endmodule
